package com.offermaster.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;

public final class InterviewDtos {

    private InterviewDtos() {}

    public record StartInterviewRequest(
            @NotBlank String resume,
            @NotBlank String jobDescription,
            @Min(0) @Max(10) Integer maxFollowUps) {}

    public record AnswerRequest(
            @NotBlank String turnId,
            @NotBlank String answer) {}
}
