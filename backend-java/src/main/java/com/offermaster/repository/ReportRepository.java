package com.offermaster.repository;

import com.offermaster.entity.Report;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface ReportRepository extends JpaRepository<Report, Long> {
    Optional<Report> findBySessionId(String sessionId);
    Optional<Report> findBySessionIdAndUserId(String sessionId, Long userId);

    /** 只查 summary 列（显式投影），避免无外层事务时物化 @Lob。 */
    @Query("select r.summary from Report r where r.sessionId = :sessionId")
    Optional<String> findSummaryBySessionId(@Param("sessionId") String sessionId);
}
