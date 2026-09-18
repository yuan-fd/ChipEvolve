# 新服务器系统核查（2026-09-18）

本文件只记录可复现的非敏感系统事实；许可证地址、账号和 token 未写入仓库。

| 项目 | 已确认结果 | 证据/复现命令 |
| --- | --- | --- |
| OS | Rocky Linux 8.10 (Green Obsidian) | `cat /etc/os-release` |
| Kernel | `4.18.0-553.115.1.el8_10.x86_64` | `uname -a` |
| CPU | 2 sockets，32 cores/socket，128 logical CPUs，Intel Xeon Platinum 8462Y+ | `lscpu` |
| 内存 | 314 GiB，总可用约 76 GiB；swap 4 GiB 已用尽 | `free -h` |
| 根盘 | XFS，约 889 GiB，总可用约 747 GiB | `df -hT` |
| `/home` | XFS，约 22 TiB，总可用约 7.8 TiB | `df -hT` |
| 当前用户 | `wjyuan`，uid/gid `1020/1022`，无额外组 | `id` |
| Python | `/usr/bin/python3`，Python 3.12.13；pip 23.2.1 | `python3 --version`; `python3 -m pip --version` |
| 编译工具 | GCC 13.3.1、GNU make 4.2.1、CMake 3.26.5、Git 2.43.7、Bash 4.4 | `gcc --version`; `make --version`; etc. |
| 容器命令 | Docker 命令由 Podman 4.9.4 提供兼容入口；未使用 | `docker --version`; `podman --version` |
| 模块系统 | Environment Modules 4.5.2，`module` 可用 | `module --version` |
| 共享目录 | 系统安装根和共享 EDA 根存在；`share`、`shared`、`eda`、`tools` 目录名未发现 | `test -d ...` |

系统层面具备继续开发和单机运行的资源条件。当前内存压力和 swap 使用量较高，真实 EDA 运行应使用资源请求并避开其他用户的高峰作业。
