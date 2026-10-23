// Simulator vision diagnostic; control is loopback-only, never physical IO.
#include <filesystem>
#include <algorithm>
#include <fstream>
#include <iostream>
#include <chrono>
#include <cmath>
#include <map>
#include <memory>
#include <optional>
#include <thread>
#include <stdexcept>

#include <nlohmann/json.hpp>
#include <yaml-cpp/yaml.h>

#include "io/gestalt/shared_frame_capture.hpp"
#include "io/gestalt/game_link.hpp"
#include "io/gestalt/shot_trial.hpp"
#include "tasks/auto_aim/aimer.hpp"
#include "tasks/auto_aim/solver.hpp"
#include "tasks/auto_aim/tracker.hpp"
#include "tasks/auto_aim/yolo.hpp"

namespace {
// Physical hardware backends are never constructed. A generation change ends
// the run instead of reusing old frames. Firing needs an explicit shot budget.
class FollowLease {
public:
  explicit FollowLease(int port, bool bench_pose = false) : link(port) {
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < until) {
      const auto current = link.find_player(0);
      if (link.connected() && current && link.match_status() == 1) {
        map = *current;
        generation = link.connection_generation();
        if (link.attr(map, io::gestalt::attr::kClass).value_or(-1) != 1003)
          throw std::runtime_error("Follow requires the live pid-0 infantry");
        if (!link.exec_if_generation("ExtAimClaim 0 1", generation)) break;
        link.exec_if_generation("UEExec RBTakeOver 0", generation);
        if (bench_pose) {
          // Restore the same fixed bench control modes after a fresh spawn.
          // Human control / chassis-follow defaults can fight external aim.
          if (!link.exec_if_generation("SetAttribute 0 50000088 1", generation) ||
              !link.exec_if_generation("SetAttribute 0 50000089 0", generation) ||
              !link.exec_if_generation("UEExec RBTeleport 0 -450 300 -95 0", generation) ||
              !link.exec_if_generation("UEExec RBExtAim 0 0 -5 0", generation))
            throw std::runtime_error("Simulator bench placement after takeover failed");
        }
        next_renew = std::chrono::steady_clock::now();
        return;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    throw std::runtime_error("Cannot claim simulator infantry");
  }
  ~FollowLease() {
    if (generation) {
      link.exec_if_generation("UEExec RBExtAim 0 " + std::to_string(yaw) + " " +
        std::to_string(pitch) + " 0", generation);
      link.exec_if_generation("ExtAimClaim 0 0", generation);
      std::this_thread::sleep_for(std::chrono::milliseconds(150));
    }
  }
  void renew() {
    if (!link.connected() || link.connection_generation() != generation ||
        link.find_player(0) != std::optional<int>(map) || link.match_status() != 1)
      throw std::runtime_error("Simulator connection/player/match changed; follow stopped");
    if (std::chrono::steady_clock::now() >= next_renew) {
      if (!link.exec_if_generation("ExtAimClaim 0 1", generation))
        throw std::runtime_error("Simulator claim renewal failed");
      next_renew = std::chrono::steady_clock::now() + std::chrono::seconds(1);
    }
  }
  void aim(double new_yaw, double new_pitch, bool fire = false) {
    renew();
    if (!std::isfinite(new_yaw) || !std::isfinite(new_pitch) || std::abs(new_pitch) > 40)
      throw std::runtime_error("Nonfinite or out-of-range follow command");
    yaw = std::remainder(new_yaw, 360.0); pitch = new_pitch;
    if (!link.exec_if_generation("UEExec RBExtAim 0 " + std::to_string(yaw) + " " +
          std::to_string(pitch) + (fire ? " 1" : " 0"), generation))
      throw std::runtime_error("Simulator aim send failed");
  }
  io::gestalt::GameLink link;
  int map = -1;
  uint64_t generation = 0;
  double yaw = 0, pitch = -5;
private:
  std::chrono::steady_clock::time_point next_renew;
};

// One round of ammunition at a time; requests are not counted as shots. The
// actual counter acknowledges launch, then HP/damage are sampled after flight.
// Target telemetry is used only for scoring/identity, never for aiming.
}

int main(int argc, char ** argv)
{
  std::string failure_stop_file, failure_status_file;
  nlohmann::json failure_progress = nlohmann::json::object();
  bool active_continuous_follow = false;
  try {
    if (argc < 2)
      throw std::runtime_error("Usage: gestalt_vision_probe <local-ws-port> [config-path] "
        "[--seconds N] [--enemy-color red|blue] [--refine-corners] [--quad-scale S] "
        "[--follow] [--shot-budget 0..50] [--bullet-speed m/s] "
        "[--sim-delay-ms 0..400] [--pitch-bias-deg -10..10] [--center-hold] "
        "[--fire-window-deg 1..15] [--settle-ms 200..2000] [--prearm] [--prepare-during-settle] [--hit-goal 0..31] [--bench-pose] "
        "[--continuous --stop-file PATH] [--output report.json]");
    const int port = std::stoi(argv[1]);
    if (port < 1 || port > 65535) throw std::runtime_error("Invalid port");
    std::string config = "configs/standard.yaml";
    double seconds = 0;
    std::string enemy_color, output_path = "logs/gestalt/model-frame.json";
    bool refine_corners = false;
    bool follow = false, continuous = false, center_hold = false;
    bool prearm = false, bench_pose = false, prepare_during_settle = false;
    std::string stop_file;
    double quad_scale = 1.0;
    int shot_budget = 0;
    int settle_ms = 1300, hit_goal = 0;
    double bullet_speed = 24.5, sim_delay_ms = 0, pitch_bias_deg = 0, fire_window_deg = 8;
    for (int i = 2; i < argc; ++i) {
      if (std::string(argv[i]) == "--seconds" && i + 1 < argc) {
        seconds = std::stod(argv[++i]);
        if (!std::isfinite(seconds) || seconds < 1 || seconds > 60)
          throw std::runtime_error("--seconds must be in [1,60]");
      } else if (std::string(argv[i]) == "--enemy-color" && i + 1 < argc) {
        enemy_color = argv[++i];
        if (enemy_color != "red" && enemy_color != "blue")
          throw std::runtime_error("--enemy-color must be red or blue");
      } else if (std::string(argv[i]) == "--refine-corners") refine_corners = true;
      else if (std::string(argv[i]) == "--follow") follow = true;
      else if (std::string(argv[i]) == "--continuous") continuous = true;
      else if (std::string(argv[i]) == "--center-hold") center_hold = true;
      else if (std::string(argv[i]) == "--prearm") prearm = true;
      else if (std::string(argv[i]) == "--prepare-during-settle") prepare_during_settle = true;
      else if (std::string(argv[i]) == "--bench-pose") bench_pose = true;
      else if (std::string(argv[i]) == "--settle-ms" && i + 1 < argc) {
        settle_ms = std::stoi(argv[++i]);
        if (settle_ms < 200 || settle_ms > 2000) throw std::runtime_error("Settle time must be in [200,2000] ms");
      }
      else if (std::string(argv[i]) == "--hit-goal" && i + 1 < argc) {
        hit_goal = std::stoi(argv[++i]);
        if (hit_goal < 0 || hit_goal > 31) throw std::runtime_error("Hit goal must be in [0,31]");
      }
      else if (std::string(argv[i]) == "--fire-window-deg" && i + 1 < argc) {
        fire_window_deg = std::stod(argv[++i]);
        if (!std::isfinite(fire_window_deg) || fire_window_deg < 1 || fire_window_deg > 15)
          throw std::runtime_error("Fire window must be in [1,15] degrees");
      }
      else if (std::string(argv[i]) == "--shot-budget" && i + 1 < argc) {
        shot_budget = std::stoi(argv[++i]);
        if (shot_budget < 0 || shot_budget > 50) throw std::runtime_error("Shot budget must be in [0,50]");
      }
      else if (std::string(argv[i]) == "--bullet-speed" && i + 1 < argc) {
        bullet_speed = std::stod(argv[++i]);
        if (!std::isfinite(bullet_speed) || bullet_speed < 5 || bullet_speed > 35)
          throw std::runtime_error("Bullet speed must be in [5,35] m/s");
      }
      else if (std::string(argv[i]) == "--sim-delay-ms" && i + 1 < argc) {
        sim_delay_ms = std::stod(argv[++i]);
        if (!std::isfinite(sim_delay_ms) || sim_delay_ms < 0 || sim_delay_ms > 400)
          throw std::runtime_error("Simulator delay must be in [0,400] ms");
      }
      else if (std::string(argv[i]) == "--pitch-bias-deg" && i + 1 < argc) {
        pitch_bias_deg = std::stod(argv[++i]);
        if (!std::isfinite(pitch_bias_deg) || std::abs(pitch_bias_deg) > 10)
          throw std::runtime_error("Simulator pitch bias must be in [-10,10] degrees");
      }
      else if (std::string(argv[i]) == "--stop-file" && i + 1 < argc) stop_file = argv[++i];
      else if (std::string(argv[i]) == "--quad-scale" && i + 1 < argc) {
        quad_scale = std::stod(argv[++i]);
        if (!std::isfinite(quad_scale) || quad_scale < .8 || quad_scale > 1.5)
          throw std::runtime_error("--quad-scale must be in [.8,1.5]");
      }
      else if (std::string(argv[i]) == "--output" && i + 1 < argc) output_path = argv[++i];
      else if (i == 2 && argv[i][0] != '-') config = argv[i];
      else throw std::runtime_error("Unknown probe argument");
    }
    if (continuous && stop_file.empty()) throw std::runtime_error("--continuous needs --stop-file");
    if (shot_budget && !follow) throw std::runtime_error("--shot-budget requires --follow");
    if ((prearm || hit_goal) && !shot_budget) throw std::runtime_error("Prearm/hit goal require an explicit shot budget");
    if (prepare_during_settle && (!prearm || !shot_budget))
      throw std::runtime_error("--prepare-during-settle requires --prearm and an explicit shot budget");
    if (hit_goal > shot_budget) throw std::runtime_error("Hit goal exceeds shot budget");
    if (!stop_file.empty() && std::filesystem::exists(stop_file))
      throw std::runtime_error("Stop file already exists; follow was not started");
    failure_stop_file = stop_file;
    failure_status_file = output_path;
    active_continuous_follow = follow && continuous;
    io::gestalt::SharedFrameCapture capture;
    if (!capture.init(port, 5000)) throw std::runtime_error("Cannot open the game's frame stream");
    auto frame = capture.grab(2000);
    if (!frame.ok) throw std::runtime_error("No complete fresh game frame");
    if (!std::isfinite(frame.fov_degrees) || frame.fov_degrees <= 0 || frame.fov_degrees >= 180)
      throw std::runtime_error("Invalid frame FOV");
    // Runtime calibration cache only. standard.yaml remains the source; the
    // rendered FOV/size replace physical camera calibration for this probe.
    auto runtime = YAML::LoadFile(config);
    const int calibrated_width = frame.img.cols, calibrated_height = frame.img.rows;
    const double calibrated_fov = frame.fov_degrees;
    const double f = frame.img.cols / (2 * std::tan(frame.fov_degrees * CV_PI / 360));
    const std::map<std::string, std::vector<double>> calibration{
      {"camera_matrix", {f, 0, frame.img.cols / 2., 0, f, frame.img.rows / 2., 0, 0, 1}},
      {"distort_coeffs", {0, 0, 0, 0, 0}},
      {"R_gimbal2imubody", {1, 0, 0, 0, 1, 0, 0, 0, 1}},
      {"R_camera2gimbal", {0, 0, 1, -1, 0, 0, 0, -1, 0}},
      {"t_camera2gimbal", {0, 0, 0}}};
    YAML::Emitter emitted;
    emitted << YAML::BeginMap;
    for (const auto & entry : runtime) {
      const auto key = entry.first.as<std::string>();
      if ((!enemy_color.empty() && key == "enemy_color") ||
          (refine_corners && key == "yolo26_refine_corners") ||
          (follow && (key == "yaw_offset" || key == "pitch_offset" ||
            key == "high_speed_delay_time"))) continue;
      if (!calibration.count(key)) emitted << YAML::Key << key << YAML::Value << entry.second;
    }
    for (const auto & entry : calibration)
      emitted << YAML::Key << entry.first << YAML::Value << YAML::Flow << entry.second;
    if (!enemy_color.empty()) emitted << YAML::Key << "enemy_color" << YAML::Value << enemy_color;
    if (refine_corners) emitted << YAML::Key << "yolo26_refine_corners" << YAML::Value << true;
    if (follow) {
      // Simulator-only mechanical zero and explicit empirical delay. Aimer
      // separately includes measured frame age and ballistic flight time.
      emitted << YAML::Key << "yaw_offset" << YAML::Value << 0.0;
      emitted << YAML::Key << "pitch_offset" << YAML::Value << 0.0;
      emitted << YAML::Key << "high_speed_delay_time" << YAML::Value << sim_delay_ms / 1000;
    }
    emitted << YAML::EndMap;
    std::filesystem::create_directories("build");
    const std::string runtime_path = "build/gestalt-vision-runtime.yaml";
    { std::ofstream out(runtime_path); out << "# Generated by gestalt_vision_probe; do not edit.\n" << emitted.c_str(); }
    auto_aim::YOLO detector(runtime_path, false);
    auto_aim::Solver solver(runtime_path);
    auto_aim::Tracker tracker(runtime_path, solver);
    auto_aim::Aimer aimer(runtime_path);
    std::unique_ptr<FollowLease> control;
    if (follow) {
      control = std::make_unique<FollowLease>(port, bench_pose);
      control->aim(0, -5);
      std::this_thread::sleep_for(std::chrono::milliseconds(400));
    }
    std::unique_ptr<io::gestalt::ShotTrial<FollowLease>> shooting;
    if (shot_budget) shooting = std::make_unique<io::gestalt::ShotTrial<FollowLease>>(
      *control, shot_budget, settle_ms, prearm, hit_goal, prepare_during_settle);
    frame = capture.grab(2000); // Discard the frame captured before model compilation.
    if (!frame.ok) throw std::runtime_error("No fresh frame after model compilation");
    nlohmann::json samples = nlohmann::json::array();
    int detection_frames = 0, tracking_frames = 0, converged_frames = 0;
    uint64_t total_frames = 0, aim_commands = 0, rejected_aim_frames = 0;
    uint32_t epoch = 0;
    const auto output_parent = std::filesystem::path(output_path).parent_path();
    if (!output_parent.empty()) std::filesystem::create_directories(output_parent);
    std::ofstream live_samples;
    if (continuous) live_samples.open(output_path + ".frames.jsonl");
    auto next_status = std::chrono::steady_clock::now();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(seconds);
    std::list<auto_aim::Armor> armors;
    auto_aim::YoloDetectionDiagnostics diagnostic;
    do {
      if (!stop_file.empty() && std::filesystem::exists(stop_file)) break;
      if (frame.img.cols != calibrated_width || frame.img.rows != calibrated_height ||
          !std::isfinite(frame.fov_degrees) || std::abs(frame.fov_degrees - calibrated_fov) > .05)
        throw std::runtime_error("Frame calibration changed during probe");
      if (control) {
        control->renew();
        const bool identity = frame.writer_process_id == capture.process_id() &&
          frame.takeover_player_id == 0 && frame.takeover_attribute_map_id == control->map &&
          frame.view_actor_unique_id != 0 && frame.view_actor_unique_id == frame.takeover_target_unique_id &&
          frame.takeover_epoch != 0 && (frame.identity_flags & 3u) == 3u;
        if (!identity || (epoch && frame.takeover_epoch != epoch))
          throw std::runtime_error("Follow camera/player identity changed");
        epoch = frame.takeover_epoch;
      }
      // SharedFrameCapture returns OpenCV BGR; the production camera/detector
      // contract is RGB, including the independent light-bar color check.
      cv::Mat rgb;
      cv::cvtColor(frame.img, rgb, cv::COLOR_BGR2RGB);
      const auto begin = std::chrono::steady_clock::now();
      armors = detector.detect(rgb, static_cast<int>(total_frames % 2000000000), true);
      diagnostic = detector.diagnostics();
      const auto & qv = frame.camera_quaternion_ue_xyzw;
      Eigen::Quaterniond q(qv[3], qv[0], qv[1], qv[2]);
      if (!q.coeffs().allFinite() || q.norm() < .5)
        throw std::runtime_error("Invalid same-frame camera quaternion");
      q.normalize();
      const Eigen::Matrix3d reflect{{1, 0, 0}, {0, -1, 0}, {0, 0, 1}};
      const Eigen::Matrix3d cv_to_ue{{0, 0, 1}, {1, 0, 0}, {0, -1, 0}};
      const Eigen::Matrix3d camera_to_gimbal{{0, 0, 1}, {-1, 0, 0}, {0, -1, 0}};
      solver.set_R_gimbal2world(Eigen::Quaterniond(
        reflect * q.toRotationMatrix() * cv_to_ue * camera_to_gimbal.transpose()));
      nlohmann::json observations = nlohmann::json::array();
      auto tracked_armors = armors;
      for (auto & armor : tracked_armors) {
        if (quad_scale != 1.0) {
          cv::Point2f center(0, 0);
          for (const auto & point : armor.points) center += point;
          center *= .25f;
          for (auto & point : armor.points) point = center + (point - center) * static_cast<float>(quad_scale);
        }
      }
      const auto t = std::chrono::steady_clock::time_point(
        std::chrono::duration_cast<std::chrono::steady_clock::duration>(
          std::chrono::duration<double>(frame.t_present)));
      auto targets = tracker.track(tracked_armors, t);
      // Tracker already solves its selected/associated detections. Reuse those
      // owned observations for diagnostics instead of repeating IPPE and the
      // 140-angle reprojection search before every tracking update.
      const auto record_observation = [&](const auto_aim::Armor & armor) {
        if (!armor.xyz_in_world.allFinite() || !armor.ypr_in_world.allFinite()) return;
        observations.push_back({{"color", auto_aim::COLORS.at(armor.color)},
          {"confidence", armor.confidence}, {"range_m", armor.xyz_in_world.norm()},
          {"xyz_m", {armor.xyz_in_world[0], armor.xyz_in_world[1], armor.xyz_in_world[2]}},
          {"normal_yaw_rad", armor.ypr_in_world[0]}});
      };
      if (tracker.frame_diagnostics().initialized_this_frame) {
        for (const auto & armor : tracked_armors) record_observation(armor);
      } else {
        for (const auto & association : tracker.last_associations())
          if (association.used_for_update && association.observation)
            record_observation(*association.observation);
      }
      nlohmann::json sample{{"frame_id", frame.frame_id}, {"capture_qpc_s", frame.t_present},
        {"detections", observations}, {"observation_source", "tracker_solved_observations"},
        {"tracker_state", tracker.state()},
        {"processing_ms", std::chrono::duration<double, std::milli>(
          std::chrono::steady_clock::now() - begin).count()}};
      if (!armors.empty()) ++detection_frames;
      bool sent_aim = false;
      if (!targets.empty()) {
        ++tracking_frames;
        auto & target = targets.front();
        const auto x = target.ekf_x();
        const bool converged = target.convergened();
        if (converged) ++converged_frames;
        sample["target"] = {{"center_m", {x[0], x[2], x[4]}},
          {"center_velocity_mps", {x[1], x[3], x[5]}},
          {"phase_rad", target.ekf().x[6]}, {"radius_m", target.ekf().x[8]},
          {"radius_delta_m", target.ekf().x[9]}, {"height_delta_m", target.ekf().x[10]},
          {"ekf_omega_rad_s", x[7]}, {"model_omega_rad_s", target.yaw_eskf_rate()},
          {"model_trusted", target.yaw_model_trusted()}, {"converged", converged},
          {"armor_count", target.armor_num()}};
        if (control && converged && tracker.state() == "tracking" && !armors.empty()) {
          auto_aim::AimPredictionTrace trace;
          const auto command = aimer.aim(targets, t, bullet_speed, true, &trace,
            auto_aim::AimGeometry::whole_ekf, auto_aim::AimTiming::high_speed);
          const Eigen::Vector3d point = aimer.debug_aim_point.xyza.head(3);
          const Eigen::Vector3d center(x[0], x[2], x[4]);
          const double point_range = point.norm();
          const double point_shift = (point - center).norm();
          const double radius_bound = std::clamp(std::max(std::abs(x[8]), std::abs(x[8] + x[9])), .05, .5);
          // Fixed-center bench: a converged flag alone can occur during a bad
          // initialization. Do not steer the camera with an untrusted state,
          // invalid ballistic trace, or a future point far outside this rotor.
          const bool aim_valid = trace.valid && trace.converged && target.yaw_model_trusted() &&
            point.allFinite() && point_range > .5 && point_shift < radius_bound + .35 &&
            std::isfinite(trace.flight_time_s) &&
            trace.flight_time_s >= .8 * point_range / bullet_speed &&
            trace.flight_time_s <= 2 * point_range / bullet_speed + .05;
          sample["aim_validation"] = {{"valid", aim_valid}, {"trace_valid", trace.valid},
            {"trace_converged", trace.converged}, {"model_trusted", target.yaw_model_trusted()},
            {"future_point_range_m", point_range}, {"future_point_center_shift_m", point_shift},
            {"rotor_radius_bound_m", radius_bound}, {"flight_time_s", trace.flight_time_s}};
          if (command.control && !aim_valid) ++rejected_aim_frames;
          if (command.control && aim_valid) {
            // Fixed-center gyro bench only: hold the visually estimated center
            // bearing and shoot as a predicted face crosses it. Never use the
            // simulator target pose/rate to construct this command.
            const double center_yaw = std::atan2(x[2], x[0]);
            const double yaw = center_hold ? -center_yaw * 180 / CV_PI : -command.yaw * 180 / CV_PI;
            const double pitch = -command.pitch * 180 / CV_PI + pitch_bias_deg;
            const auto yaw_now = control->link.attr(control->map, io::gestalt::attr::kTurretYaw);
            const auto pitch_now = control->link.attr(control->map, io::gestalt::attr::kTurretPitch);
            const double age = std::chrono::duration<double>(std::chrono::steady_clock::now() - t).count();
            const double face_alignment = std::remainder(aimer.debug_aim_point.xyza[3] - center_yaw, 2 * CV_PI);
            const bool preparation_valid = target.armor_num() == 4 &&
              std::abs(x[7]) > 3 && std::abs(x[7]) < 7 && age >= 0 && age < .15 &&
              (!shooting || settle_ms / 1000.0 >= trace.flight_time_s + .1);
            const bool qualified = preparation_valid &&
              (!center_hold || std::abs(face_alignment) < fire_window_deg * CV_PI / 180) &&
              yaw_now && pitch_now && std::abs(std::remainder(yaw - *yaw_now, 360.0)) < .6 &&
              std::abs(pitch - *pitch_now) < .6;
            const bool fire = shooting && shooting->update(qualified, preparation_valid);
            control->aim(yaw, pitch, fire);
            sent_aim = true;
            ++aim_commands;
            sample["command"] = {{"yaw_deg", yaw}, {"pitch_deg", pitch}, {"fire", fire ? 1 : 0},
              {"shot_qualified", qualified}, {"bullet_speed_mps", bullet_speed}, {"face_id", trace.face_id},
              {"face_alignment_deg", face_alignment * 180 / CV_PI}, {"center_hold", center_hold},
              {"flight_time_s", trace.flight_time_s}, {"frame_age_ms", 1000 * std::chrono::duration<double>(
                std::chrono::steady_clock::now() - t).count()}};
            if (const auto yaw_now = control->link.attr(control->map, io::gestalt::attr::kTurretYaw))
              sample["turret_yaw_deg"] = *yaw_now;
            if (const auto pitch_now = control->link.attr(control->map, io::gestalt::attr::kTurretPitch))
              sample["turret_pitch_deg"] = *pitch_now;
          }
        }
      }
      if (shooting && !sent_aim) {
        shooting->update(false);
        control->aim(control->yaw, control->pitch, false);
      }
      if (shooting) sample["shot_trial"] = shooting->status();
      ++total_frames;
      if (continuous) live_samples << sample.dump() << '\n';
      samples.push_back(std::move(sample));
      if (continuous && samples.size() > 300) samples.erase(samples.begin());
      if (continuous && std::chrono::steady_clock::now() >= next_status) {
        std::ofstream status(output_path);
        nlohmann::json progress{{"running", true}, {"frames", total_frames},
          {"detection_frames", detection_frames}, {"tracking_frames", tracking_frames},
          {"converged_frames", converged_frames}, {"aim_commands", aim_commands}, {"fire", 0},
          {"last", samples.back()}, {"camera_identity_flags", frame.identity_flags},
          {"camera_arm_cm", frame.camera_arm_length_cm}, {"stop_file", stop_file},
          {"scope", shooting ? "Bounded simulator shot diagnostic; legacy camera geometry remains unverified"
                             : "Non-firing follow; legacy boom/flags7 camera contract remains unverified"}};
        if (shooting) progress["shot_trial"] = shooting->status();
        progress["fire"] = samples.back().value("command", nlohmann::json::object()).value("fire", 0);
        progress["sim_delay_ms"] = sim_delay_ms;
        progress["pitch_bias_deg"] = pitch_bias_deg;
        progress["bullet_speed_mps"] = bullet_speed;
        progress["center_hold"] = center_hold;
        progress["fire_window_deg"] = fire_window_deg;
        progress["diagnostic_pose_reuse"] = true;
        progress["settle_ms"] = settle_ms;
        progress["prearm"] = prearm;
        progress["prepare_during_settle"] = prepare_during_settle;
        progress["hit_goal"] = hit_goal;
        progress["bench_pose"] = bench_pose;
        progress["rejected_aim_frames"] = rejected_aim_frames;
        failure_progress = progress;
        status << progress.dump(2);
        live_samples.flush();
        next_status = std::chrono::steady_clock::now() + std::chrono::seconds(1);
      }
      if (!continuous && std::chrono::steady_clock::now() >= deadline) break;
      frame = capture.grab(2000);
      if (!frame.ok) throw std::runtime_error("Frame stream stalled during probe");
    } while (true);
    nlohmann::json report{
      {"frame_id", frame.frame_id}, {"writer_pid", frame.writer_process_id},
      {"size", {frame.img.cols, frame.img.rows}}, {"fov_deg", frame.fov_degrees},
      {"camera_position_cm", frame.camera_position_ue_cm},
      {"camera_quaternion_xyzw", frame.camera_quaternion_ue_xyzw},
      {"arm_cm", frame.camera_arm_length_cm}, {"identity_flags", frame.identity_flags},
      {"view_id", frame.view_actor_unique_id}, {"target_id", frame.takeover_target_unique_id},
      {"player_id", frame.takeover_player_id}, {"map_id", frame.takeover_attribute_map_id},
      {"epoch", frame.takeover_epoch}, {"raw_rows", diagnostic.raw_rows},
      {"candidates", diagnostic.candidates}, {"max_confidence", diagnostic.max_confidence},
      {"after_nms", diagnostic.after_nms},
      {"rejected_confidence", diagnostic.rejected_confidence},
      {"rejected_type", diagnostic.rejected_type},
      {"color_direct", diagnostic.color_direct},
      {"max_class", diagnostic.max_class}, {"armors", nlohmann::json::array()},
      {"frames", total_frames}, {"detection_frames", detection_frames},
      {"tracking_frames", tracking_frames}, {"converged_frames", converged_frames},
      {"enemy_color", enemy_color.empty() ? runtime["enemy_color"].as<std::string>() : enemy_color},
      {"refine_corners_override", refine_corners},
      {"quad_scale", quad_scale}, {"aim_commands", aim_commands}, {"fire", 0},
      {"bullet_speed_mps", bullet_speed}, {"shot_budget", shot_budget},
      {"sim_delay_ms", sim_delay_ms}, {"pitch_bias_deg", pitch_bias_deg},
      {"center_hold", center_hold},
      {"fire_window_deg", fire_window_deg},
      {"diagnostic_pose_reuse", true},
      {"settle_ms", settle_ms}, {"prearm", prearm}, {"hit_goal", hit_goal},
      {"prepare_during_settle", prepare_during_settle},
      {"bench_pose", bench_pose}, {"rejected_aim_frames", rejected_aim_frames},
      {"follow", follow}, {"running", false},
      {"calibration_source", "Frame-reported FOV and dimensions; ideal pinhole assumption"},
      {"scope", shooting ? "Bounded simulator shot diagnostic; legacy camera geometry remains unverified"
                       : follow ? "Non-firing simulator follow; camera geometry, range and shooting are not validated"
                        : "Read-only vision diagnostics; camera geometry, range and shooting are not validated"}};
    if (shooting) report["shot_trial"] = shooting->status();
    cv::Mat annotated = frame.img.clone();
    for (const auto & armor : armors) {
      nlohmann::json corners = nlohmann::json::array();
      std::vector<cv::Point> polygon;
      for (const auto & point : armor.points) {
        corners.push_back({point.x, point.y});
        polygon.emplace_back(cvRound(point.x), cvRound(point.y));
      }
      report["armors"].push_back({{"class_id", armor.class_id},
        {"name", auto_aim::ARMOR_NAMES.at(armor.name)},
        {"color", auto_aim::COLORS.at(armor.color)}, {"confidence", armor.confidence},
        {"color_source", armor.color_source}, {"color_score", armor.color_score},
        {"corners", corners}});
      cv::polylines(annotated, polygon, true, cv::Scalar(0, 255, 0), 2);
      if (!polygon.empty()) cv::putText(annotated,
        auto_aim::COLORS.at(armor.color) + " " + auto_aim::ARMOR_NAMES.at(armor.name),
        polygon.front(), cv::FONT_HERSHEY_SIMPLEX, .6, cv::Scalar(0, 255, 0), 2);
    }
    std::filesystem::create_directories("data/gestalt");
    std::filesystem::create_directories("logs/gestalt");
    if (!cv::imwrite("data/gestalt/model-frame.png", frame.img) ||
        !cv::imwrite("data/gestalt/model-frame-detected.png", annotated))
      throw std::runtime_error("Cannot save frame images");
    const auto parent = std::filesystem::path(output_path).parent_path();
    if (!parent.empty()) std::filesystem::create_directories(parent);
    std::ofstream output(output_path);
    if (!output) throw std::runtime_error("Cannot write frame report");
    auto saved = report;
    saved["samples"] = std::move(samples);
    output << saved.dump(2) << '\n';
    std::cout << report.dump(2) << '\n';
    return 0;
  } catch (const std::exception & error) {
    if (active_continuous_follow) {
      // A fatal follower failure also stops the sibling target spinner. The
      // lease destructor above already sent fire=0 and released ownership.
      if (!failure_stop_file.empty()) { std::ofstream stop(failure_stop_file); stop << error.what(); }
      if (!failure_status_file.empty()) {
        std::ofstream status(failure_status_file);
        failure_progress["running"] = false;
        failure_progress["fire"] = 0;
        failure_progress["error"] = error.what();
        status << failure_progress.dump(2);
      }
    }
    std::cerr << error.what() << '\n';
    return 1;
  }
}
