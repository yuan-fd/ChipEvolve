#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_ROOT="${PROJECT_ROOT}/.tools/taiwei-official-3d"
TAIWEI_ROOT="${PROJECT_ROOT}/.external-src/taiwei-pin-3d"
ORFS_ROOT="${TOOL_ROOT}/orfs-research"
DEPS_ROOT="${TOOL_ROOT}/dependencies"
BUILD_ROOT="${TOOL_ROOT}/build-env"
INSTALL_ROOT="${ORFS_ROOT}/tools/install"
PROXY_PREFIX="${TAIWEI_GITHUB_PROXY:-https://ghproxy.net/https://github.com/}"
THREADS="${TAIWEI_BUILD_THREADS:-8}"
GCC_ROOT="${TAIWEI_GCC_ROOT:-/opt/openEuler/gcc-toolset-12/root/usr}"

ORFS_COMMIT="568eb04da9173695d6bfc1b10ba868e0b6b8a9fa"
TAIWEI_COMMIT="db20136711ed8c0cdfed67a6123d059875764abd"
OPENROAD_COMMIT="305d3ba2ddfd00591924cc586ad408179f566afe"
YOSYS_COMMIT="77005b69a2f693425294dab62c49164edb15bf10"
YOSYS_SLANG_COMMIT="64b44616a3798f07453b14ea03e4ac8a16b77313"

if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "This lock is for aarch64; refusing a different architecture." >&2
  exit 2
fi
for compiler in "${GCC_ROOT}/bin/gcc" "${GCC_ROOT}/bin/g++"; do
  [[ -x "${compiler}" ]] || { echo "Missing fixed compiler: ${compiler}" >&2; exit 2; }
done

mkdir -p "${TOOL_ROOT}" "${BUILD_ROOT}" "${DEPS_ROOT}"

fetch_commit() {
  local canonical_url="$1" commit="$2" destination="$3"
  if [[ ! -d "${destination}/.git" ]]; then
    mkdir -p "${destination}"
    git -C "${destination}" init -q
    git -C "${destination}" remote add origin "${PROXY_PREFIX}${canonical_url}"
  fi
  git -C "${destination}" -c http.version=HTTP/1.1 fetch --depth=1 origin "${commit}"
  git -C "${destination}" checkout -q --detach "${commit}"
}

fetch_commit "CODA-Team/TaiWei-Pin-3D.git" "${TAIWEI_COMMIT}" "${TAIWEI_ROOT}"
[[ -z "$(git -C "${TAIWEI_ROOT}" status --porcelain=v1)" ]] || {
  echo "Pinned TaiWei source tree is dirty: ${TAIWEI_ROOT}" >&2
  exit 2
}
fetch_commit "ieee-ceda-datc/ORFS-Research.git" "${ORFS_COMMIT}" "${ORFS_ROOT}"
git -C "${ORFS_ROOT}" sparse-checkout init --cone
git -C "${ORFS_ROOT}" sparse-checkout set etc tools flow/util
git -C "${ORFS_ROOT}" config "url.${PROXY_PREFIX}.insteadOf" https://github.com/
git -C "${ORFS_ROOT}" submodule update --init tools/OpenROAD tools/yosys tools/yosys-slang
git -C "${ORFS_ROOT}/tools/OpenROAD" config "url.${PROXY_PREFIX}.insteadOf" https://github.com/
git -C "${ORFS_ROOT}/tools/OpenROAD" submodule update --init src/sta third-party/abc
git -C "${ORFS_ROOT}/tools/yosys" config "url.${PROXY_PREFIX}.insteadOf" https://github.com/
git -C "${ORFS_ROOT}/tools/yosys" submodule update --init abc libs/cxxopts
git -C "${ORFS_ROOT}/tools/yosys-slang" config "url.${PROXY_PREFIX}.insteadOf" https://github.com/
git -C "${ORFS_ROOT}/tools/yosys-slang" submodule update --init third_party/fmt third_party/slang

[[ "$(git -C "${ORFS_ROOT}/tools/OpenROAD" rev-parse HEAD)" == "${OPENROAD_COMMIT}" ]]
[[ "$(git -C "${ORFS_ROOT}/tools/yosys" rev-parse HEAD)" == "${YOSYS_COMMIT}" ]]
[[ "$(git -C "${ORFS_ROOT}/tools/yosys-slang" rev-parse HEAD)" == "${YOSYS_SLANG_COMMIT}" ]]

INSTALLER="${BUILD_ROOT}/DependencyInstaller.openEuler.sh"
cp "${ORFS_ROOT}/tools/OpenROAD/etc/DependencyInstaller.sh" "${INSTALLER}"
patch "${INSTALLER}" < "${PROJECT_ROOT}/integrations/taiwei_pin_3d/openEuler-dependency-installer.patch"
chmod +x "${INSTALLER}"
"${INSTALLER}" -common -prefix="${DEPS_ROOT}" -skip-system-or-tools \
  -save-deps-prefixes="${BUILD_ROOT}/openroad_deps_prefixes.txt" -threads="${THREADS}"

export PATH="${DEPS_ROOT}/bin:${GCC_ROOT}/bin:${PATH}"
export LD_LIBRARY_PATH="${DEPS_ROOT}/lib:${DEPS_ROOT}/lib64:${GCC_ROOT}/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export CC="${GCC_ROOT}/bin/gcc"
export CXX="${GCC_ROOT}/bin/g++"

cmake -S "${ORFS_ROOT}/tools/OpenROAD" -B "${TOOL_ROOT}/openroad-build-gcc12" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${INSTALL_ROOT}/OpenROAD" \
  -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_BUILD_RPATH="${DEPS_ROOT}/lib;${DEPS_ROOT}/lib64;${GCC_ROOT}/lib64" \
  -DCMAKE_INSTALL_RPATH="${DEPS_ROOT}/lib;${DEPS_ROOT}/lib64;${GCC_ROOT}/lib64" \
  -DBUILD_GUI=OFF -DENABLE_TESTS=OFF -DBUILD_TESTS=OFF \
  $(<"${BUILD_ROOT}/openroad_deps_prefixes.txt")
cmake --build "${TOOL_ROOT}/openroad-build-gcc12" -j "${THREADS}"
cmake --install "${TOOL_ROOT}/openroad-build-gcc12"

make -C "${ORFS_ROOT}/tools/yosys" -j "${THREADS}" CONFIG=gcc \
  CC="${CC}" CXX="${CXX}" PREFIX="${INSTALL_ROOT}/yosys"
make -C "${ORFS_ROOT}/tools/yosys" install CONFIG=gcc \
  CC="${CC}" CXX="${CXX}" PREFIX="${INSTALL_ROOT}/yosys"
make install -C "${ORFS_ROOT}/tools/yosys-slang" -j "${THREADS}" \
  YOSYS_PREFIX="${INSTALL_ROOT}/yosys/bin/" \
  CMAKE_FLAGS="-DYOSYS_SLANG_REVISION=unknown -DSLANG_REVISION=unknown -DCMAKE_C_COMPILER=${CC} -DCMAKE_CXX_COMPILER=${CXX}"

"${INSTALL_ROOT}/OpenROAD/bin/openroad" -version
"${INSTALL_ROOT}/yosys/bin/yosys" -V
"${INSTALL_ROOT}/yosys/bin/yosys" -Q -m slang -p "help read_slang" >/dev/null
echo "TaiWei official 3D toolchain built under ${TOOL_ROOT}"
