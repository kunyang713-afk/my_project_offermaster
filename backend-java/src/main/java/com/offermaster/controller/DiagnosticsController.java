package com.offermaster.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.offermaster.service.AiClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/diag")
public class DiagnosticsController {

    private final AiClient aiClient;

    public DiagnosticsController(AiClient aiClient) {
        this.aiClient = aiClient;
    }

    @GetMapping("/python-health")
    public JsonNode pythonHealth() {
        return aiClient.probe();
    }
}
