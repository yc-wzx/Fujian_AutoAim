"""Exercise the production pose solver on projected corners; no camera or serial IO."""
import unittest

from test_lightbar_search import compile_run


class HighSpeedPoseTests(unittest.TestCase):
    def test_subdegree_search_calibration_wrap_and_excluded_targets(self):
        calibration = ("R_gimbal2imubody: [1,0,0,0,1,0,0,0,1]\n"
            "R_camera2gimbal: [0,0,1,-1,0,0,0,-1,0]\n"
            "t_camera2gimbal: [0.02,-0.01,0.005]\n"
            "camera_matrix: [1500,0,720,0,1490,540,0,0,1]\n"
            "distort_coeffs: [0.03,-0.01,0.0002,-0.0001,0.001]\n")
        compile_run(r'''
#include "tasks/auto_aim/solver.hpp"
#include "tools/math_tools.hpp"
#include <cassert>
#include <chrono>
#include <iostream>
#include <optional>
using namespace auto_aim;
constexpr double degree = M_PI / 180.;
double error(Solver & solver, const Armor & a) {
  const auto points = solver.reproject_armor(a.xyz_in_world, a.ypr_in_world[0], a.type, a.name);
  double result = 0.;
  for(int j=0;j<4;++j) result += cv::norm(points[j]-a.points[j]);
  return result;
}
int main(int argc,char **argv) {
 for(int fixture=1;fixture<argc;++fixture) {
  Solver solver(argv[fixture]); int cases=0,improved=0,excluded=0; double worst=0.;
  std::optional<Armor> timed;
  for(double heading:{-170.*degree,0.,170.*degree})
  for(double distance:{2.5,3.,4.})
  for(double phase:{-65.37,-30.37,-5.37,5.37,30.37,65.37})
  for(double noise:{0.,.12}) {
    solver.set_R_gimbal2world(Eigen::Quaterniond(
      Eigen::AngleAxisd(heading,Eigen::Vector3d::UnitZ()) *
      Eigen::AngleAxisd(.025,Eigen::Vector3d::UnitY())));
    const Eigen::Vector3d xyz(distance*std::cos(heading),distance*std::sin(heading),.08);
    const double truth=tools::limit_rad(heading+phase*degree);
    auto points=solver.reproject_armor(xyz,truth,ArmorType::small,ArmorName::three);
    for(int j=0;j<4;++j) {points[j].x+=static_cast<float>(noise*(j%2?1:-1));
                          points[j].y+=static_cast<float>(noise*(j<2?1:-1));}
    Armor original(9,.95f,cv::Rect(0,0,100,40),points);
    original.name=ArmorName::three;original.type=ArmorType::small;
    auto legacy=original,refined=original;
    solver.solve(legacy);solver.solve(refined,true);
    assert((legacy.xyz_in_world-refined.xyz_in_world).norm()<1e-12);
    assert((legacy.ypr_in_gimbal-refined.ypr_in_gimbal).norm()<1e-12);
    assert(legacy.yaw_raw==refined.yaw_raw);
    const double old_error=error(solver,legacy),new_error=error(solver,refined);
    assert(new_error<=old_error+.001);
    if(new_error<old_error-.001) ++improved;
    if(noise==0.) {
      const double angle_error=std::abs(tools::limit_rad(refined.ypr_in_world[0]-truth))/degree;
      worst=std::max(worst,angle_error);
      // Rounded non-orthogonal calibration itself biases PnP. For that
      // fixture compare the legacy objective, not an ideal-calibration truth.
      if(fixture==1) assert(angle_error<.12);
    }
    ++cases;timed=original;
  }
  assert(improved>cases/2);
  // Explicit refinement never changes outposts, balance infantry or bases.
  solver.set_R_gimbal2world(Eigen::Quaterniond::Identity());
  for(auto kind:{std::make_pair(ArmorType::small,ArmorName::outpost),
                 std::make_pair(ArmorType::big,ArmorName::three),
                 std::make_pair(ArmorType::big,ArmorName::base)}) {
    const auto points=solver.reproject_armor({3.,0.,.08},.2,kind.first,kind.second);
    Armor a(9,.95f,cv::Rect(0,0,100,40),points);a.type=kind.first;a.name=kind.second;
    auto b=a;solver.solve(a);solver.solve(b,true);
    assert((a.xyz_in_world-b.xyz_in_world).norm()==0.);
    assert((a.ypr_in_world-b.ypr_in_world).norm()==0.);++excluded;
  }
  std::array<double,2> elapsed{};
  // Informational timing only; do not assert wall-clock speed in a unit test.
  for(int mode=0;mode<2;++mode) {
    const auto begin=std::chrono::steady_clock::now();
    for(int k=0;k<150;++k) {auto sample=*timed;solver.solve(sample,mode!=0);}
    elapsed[mode]=std::chrono::duration<double,std::milli>(
      std::chrono::steady_clock::now()-begin).count()/150.;
  }
  std::cout<<"PASS pose fixture="<<fixture<<" cases="<<cases<<" improved="<<improved<<" excluded="<<excluded
    <<" noiseless_worst_deg="<<worst<<" legacy_ms="<<elapsed[0]
    <<" refined_ms="<<elapsed[1]<<"; synthetic projections, not hit rate\n";
 }
}
''', "test_high_speed_pose", sources=("tasks/auto_aim/solver.cpp",
            "tasks/auto_aim/armor.cpp", "tools/math_tools.cpp", "tools/logger.cpp"),
            libraries=("opencv_core", "opencv_imgproc", "opencv_calib3d", "yaml-cpp", "spdlog", "fmt"),
            fixtures={"pose.yaml": calibration, "rounded.yaml": calibration.replace(
                "[0,0,1,-1,0,0,0,-1,0]",
                "[0.0002,0.0001,1.0001,-1,0.00015,0.00005,0.0001,-1.0001,0.0001]")},
            arguments=("pose.yaml", "rounded.yaml"))


if __name__ == "__main__":
    unittest.main()
