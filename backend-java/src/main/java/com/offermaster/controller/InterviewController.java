package com.offermaster.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.offermaster.dto.InterviewDtos;
import com.offermaster.entity.User;
import com.offermaster.repository.UserRepository;
import com.offermaster.service.InterviewService;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping("/api/interviews")
public class InterviewController {

    private final InterviewService interviewService;
    private final UserRepository userRepository;

    public InterviewController(InterviewService interviewService, UserRepository userRepository) {
        this.interviewService = interviewService;
        this.userRepository = userRepository;
    }

    @PostMapping
    public ResponseEntity<JsonNode> start(Authentication auth,
                                          @Valid @RequestBody InterviewDtos.StartInterviewRequest req) {
        return ResponseEntity.ok(interviewService.start(userId(auth), req));
    }

    @PostMapping(value = "/stream/start", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter startStream(Authentication auth,
                                  @Valid @RequestBody InterviewDtos.StartInterviewRequest req) {
        SseEmitter emitter = new SseEmitter(0L); // 永不超时，由 AI 流结束驱动 complete
        interviewService.startStream(userId(auth), req, emitter);
        return emitter;
    }

    @PostMapping("/{sessionId}/answer")
    public ResponseEntity<JsonNode> answer(Authentication auth,
                                           @PathVariable String sessionId,
                                           @Valid @RequestBody InterviewDtos.AnswerRequest req) {
        return ResponseEntity.ok(interviewService.answer(userId(auth), sessionId, req));
    }

    @PostMapping(value = "/{sessionId}/answer/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter answerStream(Authentication auth,
                                   @PathVariable String sessionId,
                                   @Valid @RequestBody InterviewDtos.AnswerRequest req) {
        SseEmitter emitter = new SseEmitter(0L); // 永不超时，由 AI 流结束驱动 complete
        interviewService.answerStream(userId(auth), sessionId, req, emitter);
        return emitter;
    }

    @GetMapping("/{sessionId}/report")
    public ResponseEntity<JsonNode> report(Authentication auth, @PathVariable String sessionId) {
        return ResponseEntity.ok(interviewService.getReport(userId(auth), sessionId));
    }

    private Long userId(Authentication auth) {
        User user = userRepository.findByUsername(auth.getName())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        return user.getId();
    }
}
