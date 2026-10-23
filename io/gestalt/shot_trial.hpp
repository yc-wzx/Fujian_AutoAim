// Simulator-only bounded single-round diagnostic; no physical backends.
#pragma once
#include <algorithm>
#include <chrono>
#include <cmath>
#include <optional>
#include <stdexcept>
#include <string>
#include <nlohmann/json.hpp>
#include "game_link.hpp"

namespace io::gestalt {
template<class Controller, class Clock = std::chrono::steady_clock>
class ShotTrial {
public:
  ShotTrial(Controller & lease, int count, int settle_ms, bool prearm, int hit_goal,
            bool prepare_during_settle = false)
  : c(lease), budget(count), settle_ms(settle_ms), hit_goal(hit_goal), prearm(prearm),
    prepare_during_settle(prepare_during_settle) {
    if (prepare_during_settle && !prearm)
      throw std::runtime_error("Preparation during settling requires prearm");
    const auto target = c.link.find_player(92011);
    if (!target || c.link.attr(c.map, io::gestalt::attr::kTeamId).value_or(-1) != 1 ||
        c.link.attr(*target, io::gestalt::attr::kTeamId).value_or(-1) != 0 ||
        c.link.attr(*target, io::gestalt::attr::kClass).value_or(-1) != 1003)
      throw std::runtime_error("Shot trial requires the isolated blue/red infantry pair");
    target_map = *target;
    initial_bullets = value(c.map, io::gestalt::attr::kBulletsFired);
    baseline_hp = value(target_map, io::gestalt::attr::kHealth);
    baseline_damage = value(c.map, io::gestalt::attr::kDamageApplied);
    baseline_hits = value(c.map, 63000003); // BulletHitTotal, independent of HP scoring.
    baseline_taken = value(target_map, 63000001);
    clear_ammo();
    next = Clock::now() + std::chrono::seconds(2);
  }
  ~ShotTrial() {
    try { clear_ammo(); } catch (...) {}
    try { c.aim(c.yaw, c.pitch, false); }
    catch (...) {} // FollowLease still releases; native fire/claim watchdogs expire.
  }
  bool finished() const {
    return state == "complete" || state == "target_defeated" || state == "hit_goal_reached";
  }
  bool update(bool qualified, bool preparation_valid = false) {
    // A finished trial cannot re-arm, even if the target later respawns. Keep
    // following visually while retaining the completed scoring record.
    if (finished()) return false;
    const auto now = Clock::now();
    if (c.link.find_player(92011) != std::optional<int>(target_map))
      throw std::runtime_error("Shot target identity changed");
    const double total = value(c.map, io::gestalt::attr::kBulletsFired) - initial_bullets;
    if (total < actual || total > actual + (state == "firing" ? 1 : 0) || total > budget)
      throw std::runtime_error("Unexpected simulator shot counter change");
    if (state == "firing" && total > actual) {
      actual = static_cast<int>(total);
      c.aim(c.yaw, c.pitch, false);
      clear_ammo();
      event["ack_qpc_s"] = stamp(now);
      event["actual_total"] = actual;
      event["real_speed_cm_s"] = value(c.map, io::gestalt::attr::kShooterRealSpeed);
      state = "settling";
      next = now + std::chrono::milliseconds(settle_ms);
    }
    // Prepare one next round while scoring, but never fire until scoring ends.
    // Do not preload if this pending hit could finish the requested hit goal.
    if (state == "settling" && now < next && prepare_during_settle && prearm &&
        preparation_valid && !prepared_during_settle && actual < budget &&
        (!hit_goal || effective_hits + 1 < hit_goal)) {
      prepare_round(now);
      prepared_during_settle = true;
    }
    if (state == "settling" && prepared_during_settle && ammo_ready_time == typename Clock::time_point{} &&
        value(c.map, io::gestalt::attr::kReal17mm) == 1 &&
        value(c.map, io::gestalt::attr::kAllowance17mm) == 1) ammo_ready_time = now;
    if (state == "settling" && now >= next) {
      const double hp = value(target_map, io::gestalt::attr::kHealth);
      const double damage = value(c.map, io::gestalt::attr::kDamageApplied);
      const double hits = value(c.map, 63000003);
      const double taken = value(target_map, 63000001);
      event["target_hp_after"] = hp;
      event["target_hp_loss"] = baseline_hp - hp;
      event["damage_total_delta"] = damage - baseline_damage;
      event["bullet_hit_counter_delta"] = hits - baseline_hits;
      event["target_damage_taken_delta"] = taken - baseline_taken;
      event["settled_qpc_s"] = stamp(now);
      const bool effective = baseline_hp > hp && damage > baseline_damage &&
        hits - baseline_hits == 1 && std::abs((taken - baseline_taken) - (damage - baseline_damage)) < 1e-6;
      if (effective) ++effective_hits;
      event["effective_hit"] = effective;
      event["virtual_rule_hp_after"] = std::max(0, 310 - effective_hits * 10);
      events.push_back(event);
      baseline_hp = hp; baseline_damage = damage;
      baseline_hits = hits; baseline_taken = taken;
      state = hp <= 0 ? "target_defeated" : hit_goal && effective_hits >= hit_goal ? "hit_goal_reached"
        : actual == budget ? "complete" : "idle";
      if (!finished() && prepared_during_settle) state = "arming";
    }
    if (finished()) {
      clear_ammo(); // Also discard a preload after an unexpectedly lethal hit.
      prepared_during_settle = false;
      return false;
    }
    if (state != "settling" && value(target_map, io::gestalt::attr::kHealth) <= 0)
      throw std::runtime_error("Shot target defeated; trial stopped");
    if (state == "idle" && now >= next && (qualified || (prearm && preparation_valid))) {
      prepare_round(now);
      state = "arming";
    }
    if (state == "arming") {
      if (now >= arming_deadline) throw std::runtime_error("Single-round arming timed out");
      if (value(c.map, io::gestalt::attr::kReal17mm) == 1 &&
          value(c.map, io::gestalt::attr::kAllowance17mm) == 1) {
        state = "ready";
        if (ammo_ready_time == typename Clock::time_point{}) ammo_ready_time = now;
      }
    }
    if (state == "ready" && qualified) {
        event = {{"request_qpc_s", stamp(now)}, {"target_hp_before", baseline_hp},
          {"counter_before", initial_bullets + actual},
          {"ammo_requested_qpc_s", stamp(ammo_request_time)},
          {"ammo_ready_observed_qpc_s", stamp(ammo_ready_time)},
          {"prepared_during_previous_settle", prepared_during_settle}};
        prepared_during_settle = false;
        state = "firing";
        next = now + std::chrono::milliseconds(600);
    }
    if (state == "firing" && now >= next)
      throw std::runtime_error("No actual launch acknowledgement within 600 ms");
    return state == "firing" && qualified;
  }
  nlohmann::json status() const {
    return {{"budget", budget}, {"actual_shots", actual}, {"state", state},
      {"settle_ms", settle_ms}, {"prearm", prearm}, {"hit_goal", hit_goal},
      {"prepare_during_settle", prepare_during_settle},
      {"effective_hits", effective_hits}, {"virtual_rule_hp", std::max(0, 310 - effective_hits * 10)},
      {"target_player", 92011}, {"target_map", target_map}, {"shots", events},
      {"timing", "Local telemetry receipt times; not exact launch/impact timestamps"}};
  }
private:
  double value(int map, int attr) const {
    const auto v = c.link.attr(map, attr);
    if (!v || !std::isfinite(*v)) throw std::runtime_error("Shot telemetry missing");
    return *v;
  }
  static double stamp(typename Clock::time_point t) {
    return std::chrono::duration<double>(t.time_since_epoch()).count();
  }
  void prepare_round(typename Clock::time_point now) {
    set_ammo(1);
    ammo_request_time = now;
    ammo_ready_time = typename Clock::time_point{};
    arming_deadline = now + std::chrono::seconds(3);
  }
  void set_ammo(int n) {
    for (int a : {io::gestalt::attr::kAllowance17mm, io::gestalt::attr::kReal17mm})
      if (!c.link.exec_if_generation("SetAttribute 0 " + std::to_string(a) + " " +
            std::to_string(n), c.generation))
        throw std::runtime_error("Single-round ammunition command failed");
  }
  void clear_ammo() { set_ammo(0); }
  Controller & c;
  int budget, settle_ms, hit_goal, actual = 0, target_map = -1, effective_hits = 0;
  bool prearm, prepare_during_settle, prepared_during_settle = false;
  double initial_bullets = 0, baseline_hp = 0, baseline_damage = 0;
  double baseline_hits = 0, baseline_taken = 0;
  std::string state = "idle";
  typename Clock::time_point next;
  typename Clock::time_point arming_deadline{}, ammo_request_time{}, ammo_ready_time{};
  nlohmann::json event, events = nlohmann::json::array();
};

} // namespace io::gestalt
