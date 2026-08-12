package com.offermaster.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.function.BiConsumer;

/**
 * AI-brain SSE 流式消费客户端（JDK HttpClient 按行读输入流，逐帧透传）。
 *
 * 职责边界：只负责「解析 AI-brain 的 SSE 帧并回调 onEvent(eventType, data)」与
 * 「管理流生命周期（结束后发 done 事件并 complete emitter）」；
 * 具体业务处理（落库 / 幂等 / 转发给浏览器）由调用方在 onEvent 回调中完成。
 */
@Service
public class AiStreamClient {

    private final String aiBaseUrl;
    private final ObjectMapper objectMapper;
    // 强制 HTTP/1.1：JDK HttpClient 默认尝试 HTTP/2 Upgrade，AI-brain（uvicorn）不支持，
    // 会产生 "Unsupported upgrade request" 并返回 422。
    private final HttpClient httpClient = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(java.time.Duration.ofSeconds(10))
            .build();

    public AiStreamClient(@Value("${app.ai.base-url}") String aiBaseUrl, ObjectMapper objectMapper) {
        this.aiBaseUrl = aiBaseUrl;
        this.objectMapper = objectMapper;
    }

    /**
     * 代理 AI-brain 的 SSE 流到浏览器 SseEmitter。
     *
     * @param path     AI-brain 相对路径，如 /ai/interviews/{id}/answer/stream
     * @param jsonBody 请求体（JSON 字符串）
     * @param emitter  浏览器的 SseEmitter
     * @param onEvent  每解析到一帧回调一次 (eventType, data)；"error" 表示流级异常
     */
    public void stream(String path, String jsonBody, SseEmitter emitter, BiConsumer<String, JsonNode> onEvent) {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(aiBaseUrl + path))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
                .build();
        httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofInputStream())
                .thenAccept(resp -> {
                    if (resp.statusCode() != 200) {
                        try (BufferedReader r = new BufferedReader(new InputStreamReader(resp.body(), StandardCharsets.UTF_8))) {
                            StringBuilder sb = new StringBuilder();
                            String line;
                            while ((line = r.readLine()) != null) {
                                sb.append(line);
                            }
                            System.out.println("[AiStreamClient] ai returned status=" + resp.statusCode() + " body=" + sb);
                        } catch (IOException ex) {
                            System.out.println("[AiStreamClient] read error body failed: " + ex);
                        }
                        onEvent.accept("error", errorNode("ai returned status " + resp.statusCode()));
                        finish(emitter);
                        return;
                    }
                    consume(resp.body(), emitter, onEvent);
                })
                .exceptionally(ex -> {
                    onEvent.accept("error", errorNode("ai stream failed: " + ex.getMessage()));
                    finish(emitter);
                    return null;
                });
    }

    private void consume(InputStream in, SseEmitter emitter, BiConsumer<String, JsonNode> onEvent) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            String eventType = null;
            StringBuilder data = new StringBuilder();
            while ((line = reader.readLine()) != null) {
                if (line.startsWith("event:")) {
                    eventType = line.substring(6).trim();
                } else if (line.startsWith("data:")) {
                    data.setLength(0);
                    data.append(line.substring(5).trim());
                } else if (line.isEmpty() && eventType != null && data.length() > 0) {
                    onEvent.accept(eventType, parse(data.toString()));
                    eventType = null;
                    data.setLength(0);
                }
            }
        } catch (Exception e) {
            onEvent.accept("error", errorNode("ai stream read failed: " + e.getMessage()));
        } finally {
            finish(emitter);
        }
    }

    private JsonNode parse(String json) {
        try {
            return objectMapper.readTree(json);
        } catch (IOException e) {
            return errorNode("invalid sse data: " + json);
        }
    }

    private JsonNode errorNode(String detail) {
        ObjectNode node = objectMapper.createObjectNode();
        node.put("detail", detail);
        return node;
    }

    private void finish(SseEmitter emitter) {
        try {
            emitter.send(SseEmitter.event().name("done").data(objectMapper.createObjectNode().put("ok", true)));
        } catch (IOException | IllegalStateException ignored) {
            // 浏览器已断开
        }
        try {
            emitter.complete();
        } catch (IllegalStateException ignored) {
            // 已被调用方 complete
        }
    }
}
