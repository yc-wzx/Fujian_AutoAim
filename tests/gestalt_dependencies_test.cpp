// Model-free dependency smoke. No simulator, camera, serial port or controller is opened.
#include <Eigen/Dense>
#include <fmt/format.h>
#include <nlohmann/json.hpp>
#include <opencv2/opencv.hpp>
#include <openvino/openvino.hpp>
#include <openvino/opsets/opset13.hpp>
#include <spdlog/spdlog.h>
#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

int main()
{
  try {
    const auto config = YAML::Load("distance_m: 3.0\nomega_rad_s: 4.87\n");
    Eigen::Matrix3d matrix = Eigen::Matrix3d::Identity();
    matrix(0, 2) = config["distance_m"].as<double>();
    if ((matrix.inverse() * matrix - Eigen::Matrix3d::Identity()).norm() > 1e-10)
      throw std::runtime_error("Eigen calculation failed");
    cv::Mat image(32, 32, CV_8UC3, cv::Scalar(10, 20, 30));
    std::vector<unsigned char> encoded;
    if (!cv::imencode(".png", image, encoded) || cv::imdecode(encoded, cv::IMREAD_COLOR).empty())
      throw std::runtime_error("OpenCV PNG encode/decode failed");
    const std::vector<cv::Point3f> points{{-.0675f, -.0275f, 0}, {.0675f, -.0275f, 0},
                                         {.0675f, .0275f, 0}, {-.0675f, .0275f, 0}};
    cv::Mat camera = (cv::Mat_<double>(3, 3) << 1000, 0, 640, 0, 1000, 360, 0, 0, 1);
    std::vector<cv::Point2f> pixels;
    cv::projectPoints(points, cv::Vec3d(0, 0, 0), cv::Vec3d(0, 0, 3), camera, cv::Mat(), pixels);
    cv::Vec3d rotation, translation;
    if (!cv::solvePnP(points, pixels, camera, cv::Mat(), rotation, translation) ||
        std::abs(translation[2] - 3) > .001)
      throw std::runtime_error("OpenCV PnP failed");
    auto input = std::make_shared<ov::opset13::Parameter>(ov::element::f32, ov::Shape{1, 3});
    auto one = ov::opset13::Constant::create(ov::element::f32, ov::Shape{1}, {1.f});
    auto add = std::make_shared<ov::opset13::Add>(input, one);
    auto model = std::make_shared<ov::Model>(ov::OutputVector{add}, ov::ParameterVector{input});
    ov::Core core;
    nlohmann::json report{{"opencv", CV_VERSION}, {"openvino", ov::get_openvino_version().buildNumber}};
    auto devices = core.get_available_devices();
    if (std::find(devices.begin(), devices.end(), "CPU") == devices.end())
      throw std::runtime_error("OpenVINO CPU plugin not found");
    for (const auto & device : devices) {
      if (device != "CPU" && device != "GPU") continue;
      auto compiled = core.compile_model(model, device);
      auto request = compiled.create_infer_request();
      auto tensor = request.get_input_tensor();
      for (int i = 0; i < 3; ++i) tensor.data<float>()[i] = static_cast<float>(i + 1);
      request.infer();
      const auto output = request.get_output_tensor();
      for (int i = 0; i < 3; ++i)
        if (std::abs(output.data<float>()[i] - static_cast<float>(i + 2)) > .001f)
          throw std::runtime_error("OpenVINO numerical check failed on " + device);
      report["inference"][device] = "PASS";
    }
    spdlog::info("{}", fmt::format("Dependency smoke passed: {}", report.dump()));
    return 0;
  } catch (const std::exception & error) {
    std::cerr << "Dependency smoke failed: " << error.what() << '\n';
    return 1;
  }
}
