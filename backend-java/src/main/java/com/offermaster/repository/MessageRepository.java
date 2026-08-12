package com.offermaster.repository;

import com.offermaster.entity.Message;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface MessageRepository extends JpaRepository<Message, Long> {
    List<Message> findBySessionIdOrderByIdAsc(String sessionId);
    boolean existsBySessionIdAndTurnId(String sessionId, String turnId);
    void deleteBySessionIdAndTurnId(String sessionId, String turnId);

    /** 只查 content 列（原生 SQL 投影，limit 1），避免无外层事务时物化 @Lob。 */
    @Query(value = "select m.content from message m where m.session_id = :sessionId and m.role = :role order by m.id desc limit 1", nativeQuery = true)
    Optional<String> findLastContentBySessionIdAndRole(@Param("sessionId") String sessionId, @Param("role") String role);
}
