// Deterministic state-machine checks. Fake attributes/clock; no sockets or devices.
#include "io/gestalt/shot_trial.hpp"
#include <iostream>
#include <map>
#include <sstream>

using namespace std::chrono_literals;
struct Clock {
  using time_point = std::chrono::steady_clock::time_point;
  inline static time_point current{};
  static time_point now() { return current; }
};
void require(bool value, const char * message) {
  if (!value) throw std::runtime_error(message);
}
struct Link {
  std::map<std::pair<int,int>,double> values;
  bool defer_ammo = false;
  Link() {
    values = {{{1,10000036},1},{{2,10000036},0},{{2,60000002},1003},
      {{1,63000002},0},{{1,63000000},0},{{1,63000003},0},{{1,10000030},2450},
      {{2,10000003},10000},{{2,63000001},0},{{1,10000031},0},{{1,10000033},0}};
  }
  std::optional<int> find_player(int id) { return id == 92011 ? std::optional<int>(2) : std::nullopt; }
  std::optional<double> attr(int map, int key) {
    auto it = values.find({map,key});
    return it == values.end() ? std::nullopt : std::optional<double>(it->second);
  }
  bool exec_if_generation(const std::string & command, uint64_t generation) {
    std::string verb; int player,key,value;
    std::istringstream input(command); input >> verb >> player >> key >> value;
    require(verb == "SetAttribute" && player == 0 && generation == 1, "unexpected fake command");
    if (!defer_ammo || value == 0) values[{1,key}] = value;
    return true;
  }
};
struct Controller {
  Link link;
  int map = 1;
  uint64_t generation = 1;
  double yaw = 0, pitch = 0;
  void aim(double, double, bool fire) { require(!fire, "trial must only send explicit cease-fire"); }
  void launch() {
    link.values[{1,63000002}] += 1;
    link.values[{1,10000031}] = link.values[{1,10000033}] = 0;
  }
  void hit() {
    link.values[{2,10000003}] -= 20;
    link.values[{1,63000000}] += 20;
    link.values[{1,63000003}] += 1;
    link.values[{2,63000001}] += 20;
  }
  double ammo() { return link.values[{1,10000031}]; }
};
using Trial = io::gestalt::ShotTrial<Controller,Clock>;
void start(Trial & trial) {
  Clock::current += 2s;
  require(trial.update(true,true), "first qualified round should be ready");
}

int main() {
  try {
    {
      Controller c; Trial trial(c,2,300,true,0,true); start(trial);
      c.launch(); Clock::current += 50ms;
      require(!trial.update(true,true) && c.ammo() == 1, "prepare next round while holding fire");
      Clock::current += 200ms; c.hit();
      require(!trial.update(true,true), "never fire before previous score settles");
      Clock::current += 100ms;
      require(trial.update(true,true), "prepared round can fire after scoring and qualification");
      auto status=trial.status();
      require(status["shots"].size()==1 && status["shots"][0]["target_hp_loss"]==20,
              "delayed damage must remain with first shot");
      c.launch(); Clock::current += 50ms; require(!trial.update(true,true), "last shot settling");
      require(c.ammo()==0, "never prepare beyond shot budget");
      Clock::current += 300ms;
      require(!trial.update(true,true) && trial.finished() && c.ammo()==0, "budget completion clears ammo");
      require(trial.status()["shots"][1]["target_hp_loss"]==0 &&
              trial.status()["shots"][1]["prepared_during_previous_settle"]==true,
              "second miss isolated and preparation provenance retained");
    }
    {
      Controller c; Trial trial(c,50,300,true,1,true); start(trial); c.launch(); c.hit();
      Clock::current += 50ms; require(!trial.update(true,true) && c.ammo()==0, "potential goal hit must not preload");
      Clock::current += 300ms;
      require(!trial.update(true,true) && trial.status()["state"]=="hit_goal_reached", "stop on confirmed goal");
      Clock::current += 10s; require(!trial.update(true,true) && c.ammo()==0, "finished goal never rearms");
    }
    {
      Controller c; Trial trial(c,2,300,true,0); start(trial); c.launch();
      Clock::current += 50ms;
      require(!trial.update(true,true) && c.ammo()==0, "default keeps original nonoverlap behavior");
    }
    {
      Controller c; Trial trial(c,3,300,true,0,true); start(trial); c.launch();
      Clock::current += 50ms; require(!trial.update(true,false) && c.ammo()==0, "invalid preparation never loads");
      Clock::current += 50ms; require(!trial.update(true,true) && c.ammo()==1, "fresh preparation can load later");
      c.link.values[{2,10000003}]=0; Clock::current += 300ms;
      require(!trial.update(true,true) && trial.status()["state"]=="target_defeated" && c.ammo()==0,
              "native lethal hit clears prepared ammo");
    }
    {
      Controller c;
      try {
        Trial trial(c,3,300,true,0,true); start(trial); c.launch(); Clock::current += 50ms;
        trial.update(true,true); c.launch(); Clock::current += 50ms;
        trial.update(true,true); throw std::logic_error("extra launch was accepted");
      } catch (const std::runtime_error & error) {
        require(std::string(error.what()).find("Unexpected simulator shot")!=std::string::npos,
                "reject extra native launch while scoring");
      }
      require(c.ammo()==0, "exception cleanup discards all ammo");
    }
    {
      Controller c; Trial trial(c,3,300,true,0,true); start(trial); c.launch();
      c.link.defer_ammo=true; Clock::current += 50ms; trial.update(false,true);
      Clock::current += 300ms; require(!trial.update(true,true), "missing ammo ack must block firing");
      Clock::current += 3s;
      try { trial.update(true,true); throw std::logic_error("ammo timeout accepted"); }
      catch (const std::runtime_error & error) {
        require(std::string(error.what()).find("arming timed out")!=std::string::npos, "preparation timeout");
      }
    }
    std::cout << "PASS: delayed score association, no overlapping fire, budget/goal/defeat cleanup, default isolation, freshness, extra launch and ammo timeout\n";
    return 0;
  } catch (const std::exception & error) { std::cerr << error.what() << '\n'; return 1; }
}
