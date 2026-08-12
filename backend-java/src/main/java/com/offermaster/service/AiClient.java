package com.offermaster.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.offermaster.dto.InterviewDtos;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

import java.util.Map;

@Service
public class AiClient {

    private final RestClient restClient;

    public AiClient(RestClient aiRestClient) {
        this.restClient = aiRestClient;
    }

    public JsonNode probe() {
        return restClient.get().uri("/health").retrieve().body(JsonNode.class);
    }

    public JsonNode start(String sessionId, String resume, String jobDescription, int maxFollowUps) {
        Map<String, Object> body = Map.of(
                "resume", resume,
                "job_description", jobDescription,
                "max_follow_ups", maxFollowUps);
        return restClient.post()
                .uri("/ai/interviews/{id}/start", sessionId)
                .body(body)
                .retrieve()
                .body(JsonNode.class);
    }

    public JsonNode answer(String sessionId, String turnId, String answer) {
        Map<String, Object> body = Map.of("turn_id", turnId, "answer", answer);
        return restClient.post()
                .uri("/ai/interviews/{id}/answer", sessionId)
                .body(body)
                .retrieve()
                .body(JsonNode.class);
    }
}
