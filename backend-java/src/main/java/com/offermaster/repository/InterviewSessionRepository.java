package com.offermaster.repository;

import com.offermaster.entity.InterviewSession;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.util.Optional;

public interface InterviewSessionRepository extends JpaRepository<InterviewSession, String> {
    Optional<InterviewSession> findByIdAndUserId(String id, Long userId);

    /** 只查 status 列（显式投影），避免无外层事务时物化 @Lob jobDescription。 */
    @Query("select s.status from InterviewSession s where s.id = :id and s.userId = :userId")
    Optional<String> findStatusByIdAndUserId(@Param("id") String id, @Param("userId") Long userId);

    /** 只更新 status/updatedAt（JPQL update，不加载实体，避开 @Lob 物化）。 */
    @Modifying
    @Query("update InterviewSession s set s.status = :status, s.updatedAt = :updatedAt where s.id = :id")
    void updateStatus(@Param("id") String id, @Param("status") String status, @Param("updatedAt") Instant updatedAt);
}
