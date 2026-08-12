package com.offermaster.entity;

import jakarta.persistence.*;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Data
@NoArgsConstructor
@Entity
@Table(name = "message")
public class Message {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 64)
    private String sessionId;

    @Column(nullable = false, length = 16)
    private String role; // ASSISTANT | USER

    @Lob
    @Column(nullable = false)
    private String content;

    @Column(length = 64)
    private String turnId;

    @Column(nullable = false, updatable = false)
    private Instant createdAt = Instant.now();
}
