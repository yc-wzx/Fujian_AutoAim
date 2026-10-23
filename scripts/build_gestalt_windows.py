#!/usr/bin/env python3
"""Build the Windows simulator and local dependencies; never starts the aim process.

Run with Windows CPython 3.12, CMake, Ninja and an installed MSVC x64 toolchain.
Downloads, packages, binaries and logs remain under build/ and logs/.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
DEPS = BUILD / 'deps'
PREFIX = DEPS / 'install'
LOGS = ROOT / 'logs' / 'gestalt-build'
SOURCES = {
    'eigen': ('3.4.0', 'eigen-3.4.0.tar.gz', 'eigen-3.4.0',
              'https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz',
              '8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72'),
    'fmt': ('8.1.1', 'fmt-8.1.1.tar.gz', 'fmt-8.1.1',
            'https://codeload.github.com/fmtlib/fmt/tar.gz/refs/tags/8.1.1',
            '3d794d3cf67633b34b2771eb9f073bde87e846e0d395d254df7b211ef1ec7346'),
    'spdlog': ('1.9.2', 'spdlog-1.9.2.tar.gz', 'spdlog-1.9.2',
               'https://codeload.github.com/gabime/spdlog/tar.gz/refs/tags/v1.9.2',
               '6fff9215f5cb81760be4cc16d033526d1080427d236e86d70bb02994f85e3d38'),
    'yaml-cpp': ('0.7.0', 'yaml-cpp-0.7.0.tar.gz', 'yaml-cpp-yaml-cpp-0.7.0',
                 'https://codeload.github.com/jbeder/yaml-cpp/tar.gz/refs/tags/yaml-cpp-0.7.0',
                 '43e6a9fcb146ad871515f0d0873947e5d497a1c9c60c58cb102a97b47208b7c3'),
    'json': ('3.11.3', 'json-3.11.3.tar.gz', 'json-3.11.3',
             'https://codeload.github.com/nlohmann/json/tar.gz/refs/tags/v3.11.3',
             '0d8ef5af7f9794e3263480193c491549b2ba6cc74bb018906202ada498a79406'),
    'opencv': ('4.11.0', 'opencv-4.11.0.tar.gz', 'opencv-4.11.0',
               'https://codeload.github.com/opencv/opencv/tar.gz/refs/tags/4.11.0',
               '9a7c11f924eff5f8d8070e297b322ee68b9227e003fd600d4b8122198091665f'),
}


def run(command, label, env=None):
    print(f'[{label}]', flush=True)
    log = LOGS / (label + '.log')
    with log.open('w', encoding='utf-8') as handle:
        result = subprocess.run([str(value) for value in command], cwd=ROOT,
                                env=env, stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        print('\n'.join(log.read_text(encoding='utf-8', errors='replace').splitlines()[-65:]))
        raise RuntimeError(f'{label} failed ({result.returncode}); see {log}')


def msvc_environment():
    finder = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / \
        'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(finder), '-latest', '-products', '*',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
        '-property', 'installationPath'], text=True).strip()
    setup = Path(installation) / 'VC/Auxiliary/Build/vcvars64.bat'
    if not setup.is_file():
        raise RuntimeError('MSVC x64 C++ toolchain not found')
    with tempfile.TemporaryDirectory(prefix='gestalt-msvc-') as folder:
        script = Path(folder) / 'environment.cmd'
        script.write_text(f'@echo off\nchcp 65001 >nul\ncall "{setup}" >nul\n'
                          'if errorlevel 1 exit /b %errorlevel%\nset\n', encoding='utf-8')
        output = subprocess.check_output(['cmd.exe', '/d', '/c', str(script)], encoding='utf-8')
    env = dict(os.environ)
    for line in output.splitlines():
        if '=' in line and not line.startswith('='):
            key, value = line.split('=', 1)
            env[key.upper()] = value
    return env, installation


def acquire(item):
    name, (version, filename, source_dir, url, expected_hash) = item
    archive = DEPS / 'downloads' / filename
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != expected_hash:
        subprocess.run(['curl.exe', '--fail', '--location', '--retry', '3', '--connect-timeout', '20',
                        '--max-time', '240', '--silent', '--show-error', '--output', str(archive), url], check=True)
    actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise RuntimeError(f'Source checksum mismatch: {archive}')
    source = DEPS / 'src' / source_dir
    stamp = source / '.gestalt-source-sha256'
    if not stamp.is_file() or stamp.read_text().strip() != expected_hash:
        with tarfile.open(archive) as package:
            package.extractall(DEPS / 'src', filter='data')
        stamp.write_text(expected_hash + '\n', encoding='utf-8')
    print(f'[source] {name} {version}: SHA256 verified', flush=True)
    return name, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--check-models', action='store_true',
                        help='Also run the offline production YOLO/classifier integration check')
    args = parser.parse_args()
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        parser.error('Run this script with Windows CPython 3.12')
    if not 1 <= args.jobs <= 24:
        parser.error('--jobs must be in [1,24]')
    for folder in (BUILD, DEPS / 'downloads', DEPS / 'src', PREFIX, LOGS):
        folder.mkdir(parents=True, exist_ok=True)
    env, installation = msvc_environment()
    native_tools = Path(installation) / 'Common7/IDE/CommonExtensions/Microsoft/CMake'
    native_cmake = native_tools / 'CMake/bin/cmake.exe'
    native_ninja = native_tools / 'Ninja/ninja.exe'
    cmake = str(native_cmake) if native_cmake.is_file() else shutil.which('cmake')
    ninja = str(native_ninja) if native_ninja.is_file() else shutil.which('ninja')
    if not cmake or not ninja:
        raise RuntimeError('CMake and Ninja must be on PATH')
    env['VSLANG'] = '1033'
    # Native CMake supplies cmcldeps.exe for MSVC resource dependencies. The
    # MinGW CMake on some developer PATHs cannot produce a valid RC rule.
    def configure(source, directory, extra, label):
        cache = directory / 'CMakeCache.txt'
        fresh = []
        if cache.is_file():
            prior = next((line.split('=', 1)[1] for line in cache.read_text(encoding='utf-8').splitlines()
                          if line.startswith('CMAKE_COMMAND:INTERNAL=')), '')
            if Path(prior).resolve() != Path(cmake).resolve():
                fresh = ['--fresh']
        run([cmake, *fresh, '-S', source, '-B', directory, *base, *extra], label, env)
    python = BUILD / 'python/Scripts/python.exe'
    if not python.is_file():
        run([sys.executable, '-m', 'venv', BUILD / 'python'], 'python-venv')
    run([python, '-m', 'pip', 'install', '--disable-pip-version-check',
         '-r', ROOT / 'scripts/requirements_gestalt.txt'], 'python-packages')
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        sources = dict(pool.map(acquire, SOURCES.items()))
    base = ['-G', 'Ninja', f'-DCMAKE_MAKE_PROGRAM={ninja}', '-DCMAKE_C_COMPILER=cl',
            '-DCMAKE_CXX_COMPILER=cl', '-DCMAKE_BUILD_TYPE=Release',
            '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
            f'-DCMAKE_INSTALL_PREFIX={PREFIX}', f'-DCMAKE_PREFIX_PATH={PREFIX}']
    options = {
        'eigen': ['-DBUILD_TESTING=OFF', '-DEIGEN_BUILD_DOC=OFF'],
        'fmt': ['-DBUILD_SHARED_LIBS=ON', '-DFMT_TEST=OFF', '-DFMT_DOC=OFF'],
        'spdlog': ['-DSPDLOG_BUILD_SHARED=ON', '-DSPDLOG_FMT_EXTERNAL=ON',
                   '-DSPDLOG_BUILD_EXAMPLE=OFF', '-DSPDLOG_BUILD_TESTS=OFF'],
        'yaml-cpp': ['-DYAML_BUILD_SHARED_LIBS=ON', '-DYAML_CPP_BUILD_TESTS=OFF',
                     '-DYAML_CPP_BUILD_TOOLS=OFF', '-DYAML_CPP_BUILD_CONTRIB=OFF'],
        'json': ['-DJSON_BuildTests=OFF', '-DJSON_MultipleHeaders=ON'],
        'opencv': ['-DBUILD_LIST=core,imgproc,imgcodecs,highgui,videoio,calib3d,dnn',
                   '-DCMAKE_C_FLAGS=/utf-8', '-DCMAKE_CXX_FLAGS=/utf-8',
                   '-DBUILD_SHARED_LIBS=ON', '-DBUILD_TESTS=OFF', '-DBUILD_PERF_TESTS=OFF',
                   '-DBUILD_EXAMPLES=OFF', '-DBUILD_opencv_apps=OFF',
                   '-DBUILD_opencv_python2=OFF', '-DBUILD_opencv_python3=OFF',
                   '-DBUILD_JAVA=OFF', '-DWITH_FFMPEG=OFF', '-DWITH_IPP=OFF', '-DWITH_ITT=OFF',
                   '-DWITH_TBB=OFF', '-DWITH_OPENCL=OFF', '-DWITH_OPENEXR=OFF',
                   '-DOPENCV_DNN_OPENCL=OFF', '-DCPU_DISPATCH=AVX2',
                   '-DOpenCV_ARCH=x64', '-DOpenCV_RUNTIME=vc17'],
    }
    for name in SOURCES:
        directory = DEPS / (name + '-build')
        configure(sources[name], directory, options[name], name + '-configure')
        run([cmake, '--build', directory, '--parallel', args.jobs], name + '-build', env)
        run([cmake, '--install', directory], name + '-install', env)
    ov_root = Path(subprocess.check_output([python, '-c',
        'import openvino,pathlib;print(pathlib.Path(openvino.__file__).parent)'],
        encoding='utf-8', env={**env, 'PYTHONIOENCODING': 'utf-8'}).strip())
    opencv_configs = list(PREFIX.rglob('OpenCVConfig.cmake'))
    if not opencv_configs:
        raise RuntimeError('Installed OpenCV CMake configuration not found')
    opencv_dir = min(opencv_configs, key=lambda p: len(p.parts)).parent
    configure(ROOT, BUILD, [f'-DOpenCV_DIR={opencv_dir}', f'-DOpenVINO_DIR={ov_root / "cmake"}',
                           '-DOpenCV_ARCH=x64', '-DOpenCV_RUNTIME=vc17'], 'gestalt-configure')
    run([cmake, '--build', BUILD, '--target', 'gestalt', 'gestalt_dependencies_test',
         'gestalt_models_test', 'gestalt_vision_probe',
         '--parallel', args.jobs], 'gestalt-build', env)
    runtime_dirs = sorted({PREFIX / 'bin', ov_root / 'libs',
                           *[dll.parent for dll in PREFIX.rglob('opencv_core*.dll')]})
    copied = []
    for directory in runtime_dirs:
        for dll in directory.glob('*.dll'):
            shutil.copy2(dll, BUILD / dll.name)
            copied.append(dll.name)
    manifest = {'architecture': 'x64', 'configuration': 'Release', 'msvc': installation,
        'python': str(python), 'openvino': '2024.6.0', 'prefix': str(PREFIX),
        'opencv_dir': str(opencv_dir), 'openvino_dir': str(ov_root / 'cmake'),
        'runtime_dirs': [str(p) for p in runtime_dirs], 'runtime_dlls': copied,
        'sources': {name: {'version': s[0], 'url': s[3], 'sha256': s[4]} for name, s in SOURCES.items()}}
    (BUILD / 'gestalt-dependencies.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    run([BUILD / 'gestalt_dependencies_test.exe'], 'dependency-smoke', env)
    if args.check_models:
        run([BUILD / 'gestalt_models_test.exe'], 'models-integration', env)
    print(f'Built {BUILD / "gestalt.exe"}; dependency smoke passed. No aim process started.', flush=True)


if __name__ == '__main__':
    main()
