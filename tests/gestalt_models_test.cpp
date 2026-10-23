// Offline model integration check: no game, camera, serial port or control output.
#include <cmath>
#include <fstream>
#include <iostream>
#include <stdexcept>

#include <nlohmann/json.hpp>
#include <yaml-cpp/yaml.h>

#include "tasks/auto_aim/classifier.hpp"
#include "tasks/auto_aim/yolo.hpp"

int main(int argc, char ** argv)
{
  nlohmann::json report{{"status", "FAIL"}, {"input", "synthetic uniform images"}};
  try {
    if (argc > 2) throw std::runtime_error("Usage: gestalt_models_test [config-path]");
    const std::string config = argc == 2 ? argv[1] : "configs/standard.yaml";
    const auto yaml = YAML::LoadFile(config);
    if (yaml["yolo_name"].as<std::string>() != "yolo26")
      throw std::runtime_error("This check requires the production YOLO26 configuration");
    report["device"] = yaml["device"].as<std::string>();

    auto_aim::YOLO detector(config, false);
    const cv::Mat blank(720, 1280, CV_8UC3, cv::Scalar(0, 0, 0));
    for (int frame = 0; frame < 3; ++frame) {
      const auto armors = detector.detect(blank, frame, true);
      const auto diagnostics = detector.diagnostics();
      if (!diagnostics.available || diagnostics.raw_rows != 300 || !armors.empty())
        throw std::runtime_error("YOLO26 decoding or blank-frame rejection failed");
      report["yolo"] = {{"raw_rows", diagnostics.raw_rows},
                         {"accepted_on_blank", armors.size()}, {"status", "PASS"}};
    }

    auto_aim::Classifier classifier(config);
    for (const auto * backend : {"opencv", "openvino"}) {
      auto_aim::Armor armor(2, 1.0f, cv::Rect(0, 0, 32, 32),
        {{0, 0}, {31, 0}, {31, 31}, {0, 31}}, auto_aim::YOLOVersion::YOLO26);
      armor.pattern = cv::Mat(32, 32, CV_8UC3, cv::Scalar(64, 64, 64));
      if (std::string(backend) == "opencv") classifier.classify(armor);
      else classifier.ovclassify(armor);
      const int label = static_cast<int>(armor.name);
      if (label < 0 || label > 8 || !std::isfinite(armor.confidence) ||
          armor.confidence < 0 || armor.confidence > 1)
        throw std::runtime_error("Classifier returned an invalid label or confidence");
      report["classifier"][backend] = {{"status", "PASS"}, {"label", label},
                                        {"confidence", armor.confidence}};
    }
    report["status"] = "PASS";
  } catch (const std::exception & error) {
    report["error"] = error.what();
  }
  std::ofstream output("logs/gestalt-build/models-integration.json");
  if (!output) {
    std::cerr << "Cannot write logs/gestalt-build/models-integration.json\n";
    return 1;
  }
  output << report.dump(2) << '\n';
  std::cout << report.dump(2) << '\n';
  return report["status"] == "PASS" ? 0 : 1;
}
