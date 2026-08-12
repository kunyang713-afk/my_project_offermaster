package com.offermaster.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.offermaster.dto.InterviewDtos;
import com.offermaster.entity.InterviewSession;
import com.offermaster.entity.Message;
import com.offermaster.entity.Report;
import com.offermaster.entity.Resume;
import com.offermaster.repository.InterviewSessionRepository;
import com.offermaster.repository.MessageRepository;
import com.offermaster.repository.ReportRepository;
import com.offermaster.repository.ResumeRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

@Service
public class InterviewService {

    private final ResumeRepository resumeRepository;
    private final InterviewSessionRepository sessionRepository;
    private final MessageRepository messageRepository;
    private final ReportRepository reportRepository;
    private final MaskingService maskingService;
    private final AiClient aiClient;
    private final AiStreamClient aiStreamClient;
    private final ObjectMapper objectMapper;
    private final TransactionTemplate transactionTemplate;

    private final ConcurrentHashMap<String, Object> sessionLocks = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, Long> lastTurn = new ConcurrentHashMap<>();
    // SSE 流式串行门闩：每轮结束才放行下一轮，防止同一 thread_id 并发跑 LangGraph。
    private final ConcurrentHashMap<String, CompletableFuture<Void>> sessionTurnGates = new ConcurrentHashMap<>();

    public InterviewService(ResumeRepository resumeRepository,
                            InterviewSessionRepository sessionRepository,
                            MessageRepository messageRepository,
                            ReportRepository reportRepository,
                            MaskingService maskingService,
                            AiClient aiClient,
                            AiStreamClient aiStreamClient,
                            ObjectMapper objectMapper,
                            TransactionTemplate transactionTemplate) {
        this.resumeRepository = resumeRepository;
        this.sessionRepository = sessionRepository;
        this.messageRepository = messageRepository;
        this.reportRepository = reportRepository;
        this.maskingService = maskingService;
        this.aiClient = aiClient;
        this.aiStreamClient = aiStreamClient;
        this.objectMapper = objectMapper;
        this.transactionTemplate = transactionTemplate;
    }

    @Transactional
    public JsonNode start(Long userId, InterviewDtos.StartInterviewRequest req) {
        String masked = maskingService.mask(req.resume());
        Resume resume = new Resume();
        resume.setUserId(userId);
        resume.setContent(masked);
        resumeRepository.save(resume);

        String sessionId = UUID.randomUUID().toString();
        InterviewSession session = new InterviewSession();
        session.setId(sessionId);
        session.setUserId(userId);
        session.setResumeId(resume.getId());
        session.setJobDescription(req.jobDescription());
        session.setMaxFollowUps(nvl(req.maxFollowUps(), 2));
        sessionRepository.save(session);

        JsonNode ai = aiClient.start(sessionId, masked, req.jobDescription(), session.getMaxFollowUps());
        saveMessage(sessionId, "ASSISTANT", ai.path("question").asText(), "start");

        ObjectNode out = objectMapper.createObjectNode();
        out.put("sessionId", sessionId);
        out.set("ai", ai);
        return out;
    }

    @Transactional
    public JsonNode answer(Long userId, String sessionId, InterviewDtos.AnswerRequest req) {
        Object lock = sessionLocks.computeIfAbsent(sessionId, k -> new Object());
        synchronized (lock) {
            InterviewSession session = sessionRepository.findByIdAndUserId(sessionId, userId)
                    .orElseThrow(() -> new IllegalArgumentException("会话不存在或无权访问"));
            if (!"ACTIVE".equals(session.getStatus())) {
                throw new IllegalArgumentException("会话已结束");
            }
            long turn = Long.parseLong(req.turnId());
            Long last = lastTurn.get(sessionId);
            if (last != null && turn <= last) {
                throw new IllegalArgumentException("违反顺序：turn_id 必须严格递增");
            }
            if (messageRepository.existsBySessionIdAndTurnId(sessionId, req.turnId())) {
                return currentState(sessionId); // 幂等重发：直接返回当前状态
            }

            saveMessage(sessionId, "USER", req.answer(), req.turnId());
            JsonNode ai = aiClient.answer(sessionId, req.turnId(), req.answer());

            if ("done".equals(ai.path("status").asText())) {
                JsonNode report = ai.path("report");
                Report r = new Report();
                r.setSessionId(sessionId);
                r.setUserId(userId);
                r.setSummary(report.path("summary").asText());
                r.setWrongQuestionsJson(report.path("wrong_questions").toString());
                reportRepository.save(r);
                session.setStatus("COMPLETED");
                session.setUpdatedAt(Instant.now());
                sessionRepository.save(session);
            } else {
                saveMessage(sessionId, "ASSISTANT", ai.path("question").asText(), null);
            }
            lastTurn.put(sessionId, turn);
            return ai;
        }
    }

    @Transactional(readOnly = true)
    public JsonNode currentState(String sessionId) {
        return reportRepository.findBySessionId(sessionId)
                .map(r -> {
                    ObjectNode out = objectMapper.createObjectNode();
                    out.put("status", "done");
                    ObjectNode report = objectMapper.createObjectNode();
                    report.put("summary", r.getSummary());
                    out.set("report", report);
                    return (JsonNode) out;
                })
                .orElseGet(() -> lastAssistantQuestion(sessionId));
    }

    @Transactional(readOnly = true)
    public JsonNode getReport(Long userId, String sessionId) {
        sessionRepository.findByIdAndUserId(sessionId, userId)
                .orElseThrow(() -> new IllegalArgumentException("会话不存在或无权访问"));
        Report report = reportRepository.findBySessionIdAndUserId(sessionId, userId)
                .orElseThrow(() -> new IllegalArgumentException("报告不存在或尚未生成"));
        ObjectNode out = objectMapper.createObjectNode();
        out.put("sessionId", sessionId);
        out.put("summary", report.getSummary());
        out.put("wrong_questions", report.getWrongQuestionsJson());
        return out;
    }

    private JsonNode lastAssistantQuestion(String sessionId) {
        ObjectNode out = objectMapper.createObjectNode();
        out.put("status", "need_answer");
        List<Message> messages = messageRepository.findBySessionIdOrderByIdAsc(sessionId);
        String question = null;
        for (Message m : messages) {
            if ("ASSISTANT".equals(m.getRole())) {
                question = m.getContent();
            }
        }
        out.put("question", question == null ? "" : question);
        return out;
    }

    /** 流式幂等重发用：投影读取当前状态（不加载 @Lob 实体），语义与 currentState 一致。 */
    private JsonNode streamCurrentState(String sessionId) {
        Optional<String> summary = reportRepository.findSummaryBySessionId(sessionId);
        if (summary.isPresent()) {
            ObjectNode out = objectMapper.createObjectNode();
            out.put("status", "done");
            ObjectNode report = objectMapper.createObjectNode();
            report.put("summary", summary.get());
            out.set("report", report);
            return out;
        }
        ObjectNode out = objectMapper.createObjectNode();
        out.put("status", "need_answer");
        String question = messageRepository.findLastContentBySessionIdAndRole(sessionId, "ASSISTANT").orElse("");
        out.put("question", question);
        return out;
    }

    private void saveMessage(String sessionId, String role, String content, String turnId) {
        Message msg = new Message();
        msg.setSessionId(sessionId);
        msg.setRole(role);
        msg.setContent(content);
        msg.setTurnId(turnId);
        messageRepository.save(msg);
    }

    private static int nvl(Integer v, int def) {
        return v == null ? def : v;
    }

    // ================= SSE 流式入口 =================
    // 与阻塞路径业务语义一致；不启用 @Transactional（SseEmitter 在异步线程完成，
    // 事务与请求线程生命周期冲突），每个 repository.save() 独立提交，
    // 失败时回删刚写入的 USER 消息保证同 turnId 可重试。

    public void startStream(Long userId, InterviewDtos.StartInterviewRequest req, SseEmitter emitter) {
        try {
            String masked = maskingService.mask(req.resume());
            Resume resume = new Resume();
            resume.setUserId(userId);
            resume.setContent(masked);
            resumeRepository.save(resume);

            String sessionId = UUID.randomUUID().toString();
            InterviewSession session = new InterviewSession();
            session.setId(sessionId);
            session.setUserId(userId);
            session.setResumeId(resume.getId());
            session.setJobDescription(req.jobDescription());
            session.setMaxFollowUps(nvl(req.maxFollowUps(), 2));
            sessionRepository.save(session);

            String jsonBody = objectMapper.writeValueAsString(Map.of(
                    "resume", masked,
                    "job_description", req.jobDescription(),
                    "max_follow_ups", session.getMaxFollowUps()));

            aiStreamClient.stream("/ai/interviews/" + sessionId + "/start/stream", jsonBody, emitter,
                    (eventType, payload) -> handleStartEvent(sessionId, emitter, eventType, payload));
        } catch (Exception e) {
            fail(emitter, "start stream failed: " + e.getMessage());
        }
    }

    public void answerStream(Long userId, String sessionId, InterviewDtos.AnswerRequest req, SseEmitter emitter) {
        // 串行门闩：等上一轮流式结束再开始新一轮，防同一 thread_id 并发跑图。
        CompletableFuture<Void> gate = new CompletableFuture<>();
        CompletableFuture<Void> prev = sessionTurnGates.put(sessionId, gate);
        if (prev != null) {
            try {
                prev.get(3, TimeUnit.MINUTES);
            } catch (Exception ignored) {
                // 超时/中断：继续尝试，LangGraph checkpoint 层兜底。
            }
        }
        try {
            // 投影查询只取 status 列，避免无外层事务时物化 @Lob jobDescription。
            String status = sessionRepository.findStatusByIdAndUserId(sessionId, userId)
                    .orElseThrow(() -> new IllegalArgumentException("会话不存在或无权访问"));
            if (!"ACTIVE".equals(status)) {
                throw new IllegalArgumentException("会话已结束");
            }
            long turn = Long.parseLong(req.turnId());
            // 幂等检查必须在顺序检查之前：网络中断后前端重试相同 turnId 时，
            // 若上一轮已在 Java 侧处理完成（lastTurn 已更新），顺序检查会拦截；
            // 先查幂等可正确返回当前状态而非报错。
            if (messageRepository.existsBySessionIdAndTurnId(sessionId, req.turnId())) {
                // 幂等重发：直接返回当前状态（投影读取，不加载 @Lob 实体）。
                forward(emitter, "result", streamCurrentState(sessionId));
                forward(emitter, "done", objectMapper.createObjectNode().put("ok", true));
                emitter.complete();
                gate.complete(null);
                return;
            }
            Long last = lastTurn.get(sessionId);
            if (last != null && turn <= last) {
                throw new IllegalArgumentException("违反顺序：turn_id 必须严格递增");
            }
            saveMessage(sessionId, "USER", req.answer(), req.turnId());

            String jsonBody = objectMapper.writeValueAsString(Map.of("turn_id", req.turnId(), "answer", req.answer()));
            aiStreamClient.stream("/ai/interviews/" + sessionId + "/answer/stream", jsonBody, emitter,
                    (eventType, payload) -> handleAnswerEvent(userId, sessionId, req.turnId(), turn, emitter, gate, eventType, payload));
        } catch (Exception e) {
            fail(emitter, "answer stream failed: " + e.getMessage());
            gate.complete(null);
        }
    }

    private void handleStartEvent(String sessionId, SseEmitter emitter, String eventType, JsonNode payload) {
        try {
            if ("stage".equals(eventType)) {
                forward(emitter, "stage", payload);
            } else if ("token".equals(eventType)) {
                forward(emitter, "token", payload);
            } else if ("result".equals(eventType)) {
                // 注入 sessionId，前端据此开启后续轮次。
                ((ObjectNode) payload).put("sessionId", sessionId);
                saveMessage(sessionId, "ASSISTANT", payload.path("question").asText(), null);
                forward(emitter, "result", payload);
            } else if ("error".equals(eventType)) {
                forward(emitter, "error", payload);
            }
        } catch (Exception e) {
            fail(emitter, "handle start event failed: " + e.getMessage());
        }
    }

    private void handleAnswerEvent(Long userId, String sessionId, String turnId, long turn, SseEmitter emitter,
                                   CompletableFuture<Void> gate, String eventType, JsonNode payload) {
        try {
            if ("stage".equals(eventType)) {
                forward(emitter, "stage", payload);
                return;
            }
            if ("token".equals(eventType)) {
                forward(emitter, "token", payload);
                return;
            }
            if ("result".equals(eventType)) {
                if ("done".equals(payload.path("status").asText())) {
                    JsonNode report = payload.path("report");
                    // @Modifying 查询需事务包裹，用 TransactionTemplate 编程式事务
                    // （SseEmitter 回调在异步线程，无法用 @Transactional 注解）。
                    transactionTemplate.executeWithoutResult(status -> {
                        Report r = new Report();
                        r.setSessionId(sessionId);
                        r.setUserId(userId);
                        r.setSummary(report.path("summary").asText());
                        r.setWrongQuestionsJson(report.path("wrong_questions").toString());
                        reportRepository.save(r);
                        sessionRepository.updateStatus(sessionId, "COMPLETED", Instant.now());
                    });
                } else {
                    saveMessage(sessionId, "ASSISTANT", payload.path("question").asText(), null);
                }
                lastTurn.put(sessionId, turn);
                forward(emitter, "result", payload);
                gate.complete(null);
                return;
            }
            if ("error".equals(eventType)) {
                // 回删本次 USER 消息，保证同 turnId 可重试。
                messageRepository.deleteBySessionIdAndTurnId(sessionId, turnId);
                forward(emitter, "error", payload);
                gate.complete(null);
            }
        } catch (Exception e) {
            fail(emitter, "handle answer event failed: " + e.getMessage());
            gate.complete(null);
        }
    }

    private void forward(SseEmitter emitter, String name, JsonNode data) {
        try {
            emitter.send(SseEmitter.event().name(name).data(data));
        } catch (IOException | IllegalStateException e) {
            // 浏览器已断开，忽略。
        }
    }

    private void fail(SseEmitter emitter, String detail) {
        ObjectNode node = objectMapper.createObjectNode();
        node.put("detail", detail);
        forward(emitter, "error", node);
        try {
            emitter.complete();
        } catch (IllegalStateException ignored) {
            // 已被 complete。
        }
    }
}

