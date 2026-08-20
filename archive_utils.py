# Vibe Coding Agent - 安全解压缩工具
# Copyright (c) 2026 xingluosama
#
# 防护措施：
#   1. 压缩率 ≤ 6 倍（解压后总大小 / 压缩包大小）
#   2. 嵌套深度 ≤ 5 层（解压后若含压缩包，继续解压，最多 5 层）
#   3. 单文件最大 500MB
#   4. 文件总数上限 10000
#   5. 超限时弹窗提示用户手动解压

import os
import zipfile
import tarfile
import shutil
import platform
from pathlib import Path
from typing import Optional, List, Tuple

# ── 限制常量 ──
MAX_COMPRESSION_RATIO = 6        # 压缩率 ≤ 6
MAX_NESTING_DEPTH = 5            # 嵌套深度 ≤ 5
MAX_SINGLE_FILE_SIZE = 500 * 1024 * 1024  # 单文件 500MB
MAX_TOTAL_FILES = 10000          # 文件总数上限


def _popup_warning(title: str, message: str) -> bool:
    """Windows 原生弹窗。返回 True 表示用户选择"是"（手动处理）。"""
    system = platform.system()
    if system == "Windows":
        try:
            import ctypes
            result = ctypes.windll.user32.MessageBoxW(
                0, message, title,
                0x04 | 0x30  # MB_YESNO | MB_ICONWARNING
            )
            return result == 6  # IDYES
        except Exception:
            pass
    # 非 Windows 或 ctypes 不可用：通过 print 输出警告
    print(f"\n{'='*60}")
    print(f"[WARNING] {title}")
    print(f"{message}")
    print(f"{'='*60}\n")
    return False


def _is_archive_file(filepath: str) -> bool:
    """判断文件是否是支持的压缩包格式。"""
    name = os.path.basename(filepath).lower()
    return (
        name.endswith('.zip') or
        name.endswith('.tar') or
        name.endswith('.tar.gz') or
        name.endswith('.tgz') or
        name.endswith('.tar.bz2') or
        name.endswith('.tbz2') or
        name.endswith('.7z')
    )


def _get_archive_files_count(filepath: str) -> int:
    """获取压缩包内文件数量。"""
    name = os.path.basename(filepath).lower()
    try:
        if name.endswith('.zip'):
            with zipfile.ZipFile(filepath, 'r') as zf:
                return len(zf.namelist())
        elif name.endswith('.tar') or name.endswith('.tar.gz') or name.endswith('.tgz') or name.endswith('.tar.bz2') or name.endswith('.tbz2'):
            with tarfile.open(filepath, 'r:*') as tf:
                return len(tf.getnames())
    except Exception:
        pass
    return 0


def _get_archive_uncompressed_size(filepath: str) -> int:
    """获取压缩包内文件解压后的总大小（字节）。"""
    total = 0
    name = os.path.basename(filepath).lower()
    try:
        if name.endswith('.zip'):
            with zipfile.ZipFile(filepath, 'r') as zf:
                for info in zf.infolist():
                    total += info.file_size
        elif name.endswith('.tar'):
            with tarfile.open(filepath, 'r:') as tf:
                for member in tf.getmembers():
                    total += member.size
        elif name.endswith(('.tar.gz', '.tgz')):
            with tarfile.open(filepath, 'r:gz') as tf:
                for member in tf.getmembers():
                    total += member.size
        elif name.endswith(('.tar.bz2', '.tbz2')):
            with tarfile.open(filepath, 'r:bz2') as tf:
                for member in tf.getmembers():
                    total += member.size
    except Exception:
        pass
    return total


def _extract_single_archive(source_path: str, dest_dir: str) -> List[str]:
    """解压单个压缩包到目标目录。返回解压出的文件路径列表。

    支持 .zip / .tar / .tar.gz / .tgz / .tar.bz2 / .tbz2。
    """
    name = os.path.basename(source_path).lower()
    extracted_files = []

    if name.endswith('.zip'):
        with zipfile.ZipFile(source_path, 'r') as zf:
            # 安全检查：路径穿越防护
            for member in zf.namelist():
                member_path = os.path.normpath(os.path.join(dest_dir, member))
                dest_real = os.path.normpath(os.path.realpath(dest_dir))
                member_real = os.path.normpath(os.path.realpath(member_path))
                if not member_real.startswith(dest_real + os.sep) and member_real != dest_real:
                    raise ValueError(f"路径穿越检测: {member} 试图逃逸目标目录")
                if member.endswith('/') or member.endswith('\\'):
                    continue  # 跳过目录条目
                # 检查单文件大小
                info = zf.getinfo(member)
                if info.file_size > MAX_SINGLE_FILE_SIZE:
                    raise ValueError(
                        f"单文件过大: {member} ({info.file_size / 1024 / 1024:.1f} MB > "
                        f"{MAX_SINGLE_FILE_SIZE / 1024 / 1024:.0f} MB)"
                    )
            zf.extractall(dest_dir)
            extracted_files = [
                os.path.join(dest_dir, m)
                for m in zf.namelist()
                if not m.endswith('/') and not m.endswith('\\')
            ]

    elif name.endswith('.tar'):
        with tarfile.open(source_path, 'r:') as tf:
            _check_tar_members(tf, dest_dir)
            tf.extractall(dest_dir)
            extracted_files = [
                os.path.join(dest_dir, m.name)
                for m in tf.getmembers()
                if m.isfile()
            ]

    elif name.endswith(('.tar.gz', '.tgz')):
        with tarfile.open(source_path, 'r:gz') as tf:
            _check_tar_members(tf, dest_dir)
            tf.extractall(dest_dir)
            extracted_files = [
                os.path.join(dest_dir, m.name)
                for m in tf.getmembers()
                if m.isfile()
            ]

    elif name.endswith(('.tar.bz2', '.tbz2')):
        with tarfile.open(source_path, 'r:bz2') as tf:
            _check_tar_members(tf, dest_dir)
            tf.extractall(dest_dir)
            extracted_files = [
                os.path.join(dest_dir, m.name)
                for m in tf.getmembers()
                if m.isfile()
            ]

    elif name.endswith('.7z'):
        raise ValueError(
            "7z 格式需要手动解压。请使用 7-Zip 或类似工具解压后，"
            "将文件放入工作区。"
        )

    return extracted_files


def _check_tar_members(tf: tarfile.TarFile, dest_dir: str):
    """检查 tar 成员安全性。"""
    dest_real = os.path.normpath(os.path.realpath(dest_dir))
    for member in tf.getmembers():
        member_path = os.path.normpath(os.path.join(dest_dir, member.name))
        member_real = os.path.normpath(os.path.realpath(member_path))
        if not member_real.startswith(dest_real + os.sep) and member_real != dest_real:
            raise ValueError(f"路径穿越检测: {member.name} 试图逃逸目标目录")
        if member.isfile() and member.size > MAX_SINGLE_FILE_SIZE:
            raise ValueError(
                f"单文件过大: {member.name} ({member.size / 1024 / 1024:.1f} MB > "
                f"{MAX_SINGLE_FILE_SIZE / 1024 / 1024:.0f} MB)"
            )


def unpack_archive(source_path: str, dest_dir: Optional[str] = None) -> str:
    """安全解压缩入口。

    Args:
        source_path: 压缩包路径（绝对路径）
        dest_dir: 目标目录。默认解压到压缩包同级目录。

    Returns:
        解压结果描述字符串。

    安全限制：
        - 压缩率 ≤ 6
        - 嵌套深度 ≤ 5
        - 单文件 ≤ 500MB
        - 总文件数 ≤ 10000
    """
    if not os.path.isfile(source_path):
        return f"Error: 文件不存在: {source_path}"

    if dest_dir is None:
        dest_dir = os.path.dirname(source_path)

    os.makedirs(dest_dir, exist_ok=True)

    source_path = os.path.abspath(source_path)
    dest_dir = os.path.abspath(dest_dir)
    archive_name = os.path.basename(source_path)

    # ── 检查是否为支持的格式 ──
    if not _is_archive_file(source_path):
        return (
            f"Error: 不支持的文件格式: {archive_name}\n"
            f"支持的格式: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tbz2, .7z\n"
            f"对于 .7z 格式，请使用 7-Zip 手动解压。"
        )

    # ── 压缩率预检（仅对可能构成威胁的大压缩包检查）──
    compressed_size = os.path.getsize(source_path)
    if compressed_size == 0:
        return "Error: 压缩包为空文件"

    uncompressed_size = _get_archive_uncompressed_size(source_path)
    # 只有解压后 > 10MB 时才做压缩率检查（小文件高压缩率是正常的）
    if uncompressed_size > 0 and uncompressed_size > 10 * 1024 * 1024:
        ratio = uncompressed_size / compressed_size
        if ratio > MAX_COMPRESSION_RATIO:
            ratio_str = f"{ratio:.1f}"
            msg = (
                f"警告: 压缩率过高 ({ratio_str}:1)，可能为 Zip Bomb！\n\n"
                f"压缩包: {archive_name}\n"
                f"压缩大小: {compressed_size / 1024:.1f} KB\n"
                f"解压大小: {uncompressed_size / 1024 / 1024:.1f} MB\n"
                f"压缩率: {ratio_str}:1（超过限制 {MAX_COMPRESSION_RATIO}:1）\n\n"
                f"为了系统安全，NORP Agent 拒绝自动解压此文件。\n"
                f"请手动使用 7-Zip / WinRAR 等工具解压。"
            )
            _popup_warning("⚠️ Zip Bomb 检测 — NORP Agent", msg)
            return (
                f"Error: 压缩率过高 ({ratio_str}:1 > {MAX_COMPRESSION_RATIO}:1)，疑似 Zip Bomb。\n"
                f"已弹窗提示，请手动解压: {source_path}"
            )

    # ── 文件数量预检 ──
    file_count = _get_archive_files_count(source_path)
    if file_count > MAX_TOTAL_FILES:
        return (
            f"Error: 文件数量过多 ({file_count} > {MAX_TOTAL_FILES})。\n"
            f"请手动解压: {source_path}"
        )

    # ── 解压 ──
    try:
        # 第一层解压
        extracted = _extract_single_archive(source_path, dest_dir)
        total_files = len(extracted)
        total_size = sum(os.path.getsize(f) for f in extracted if os.path.isfile(f))

        # ── 嵌套解压（最多 MAX_NESTING_DEPTH 层）──
        current_depth = 1
        to_process = [f for f in extracted if _is_archive_file(f)]

        while to_process and current_depth < MAX_NESTING_DEPTH:
            next_batch = []
            for nested_archive in to_process:
                nested_name = os.path.basename(nested_archive)
                nested_dest = os.path.dirname(nested_archive)

                # 嵌套压缩率预检
                n_compressed = os.path.getsize(nested_archive)
                n_uncompressed = _get_archive_uncompressed_size(nested_archive)
                if n_uncompressed > 0 and n_uncompressed > 10 * 1024 * 1024:
                    n_ratio = n_uncompressed / n_compressed
                    if n_ratio > MAX_COMPRESSION_RATIO:
                        print(f"[ArchiveUtils] 跳过嵌套压缩包（压缩率 {n_ratio:.1f}:1）: {nested_name}")
                        continue

                try:
                    n_extracted = _extract_single_archive(nested_archive, nested_dest)
                    total_files += len(n_extracted)
                    total_size += sum(
                        os.path.getsize(f) for f in n_extracted if os.path.isfile(f)
                    )
                    next_batch.extend(
                        f for f in n_extracted if _is_archive_file(f)
                    )
                    # 删除已解压的嵌套压缩包
                    try:
                        os.remove(nested_archive)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[ArchiveUtils] 嵌套解压失败 ({nested_name}): {e}")
                    continue

            to_process = next_batch
            current_depth += 1

        if to_process:
            # 达到最大嵌套深度，仍有未解压的嵌套压缩包
            remaining = len(to_process)
            msg = (
                f"警告: 检测到深度嵌套压缩包！\n\n"
                f"压缩包: {archive_name}\n"
                f"已达到最大嵌套深度 {MAX_NESTING_DEPTH} 层，"
                f"仍有 {remaining} 个压缩包未解压。\n\n"
                f"请手动解压剩余的嵌套压缩包。"
            )
            _popup_warning("⚠️ 嵌套深度超限 — NORP Agent", msg)

        # ── 最终压缩率验证（仅 >10MB 时检查）──
        if compressed_size > 0 and total_size > 10 * 1024 * 1024:
            final_ratio = total_size / compressed_size
            if final_ratio > MAX_COMPRESSION_RATIO:
                return (
                    f"Error: 解压后总压缩率 ({final_ratio:.1f}:1) 超过限制。\n"
                    f"请手动解压: {source_path}"
                )

        return (
            f"解压完成: {archive_name}\n"
            f"解压位置: {dest_dir}\n"
            f"文件数: {total_files}\n"
            f"总大小: {total_size / 1024 / 1024:.1f} MB\n"
            f"嵌套深度: {current_depth}/{MAX_NESTING_DEPTH}"
        )

    except ValueError as e:
        return f"Error: {str(e)}"
    except zipfile.BadZipFile:
        return f"Error: 损坏的 ZIP 文件: {archive_name}"
    except tarfile.TarError as e:
        return f"Error: 损坏的 TAR 文件: {str(e)}"
    except Exception as e:
        return f"Error: 解压失败: {str(e)}"
