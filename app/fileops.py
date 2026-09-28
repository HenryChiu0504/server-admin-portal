#!/usr/bin/env python3
"""File operations for the web file manager.

The Portal backend runs as root but never touches user files itself: it
starts this script through `setpriv` as the logged-in Linux user, so the
kernel enforces that user's permissions for every operation.

Usage: fileops.py <command> [args...]
Output: JSON on stdout (or raw bytes for `cat` / `zip`).
Errors: a line "E <json>" on stderr and exit status 1.
Progress (zip only): lines "P <done_bytes> <total_bytes>" on stderr.
"""
import json
import os
import shutil
import stat
import sys
import time
import zipfile

os.umask(0o022)
TEXT_LIMIT = 2 * 1024 * 1024


def fail(message):
    sys.stderr.write("E " + json.dumps({"error": message}, ensure_ascii=False) + "\n")
    sys.exit(1)


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def describe(err):
    if isinstance(err, PermissionError):
        return "沒有權限"
    if isinstance(err, FileNotFoundError):
        return "找不到檔案或資料夾"
    if isinstance(err, FileExistsError):
        return "已經有同名的檔案或資料夾"
    if isinstance(err, IsADirectoryError):
        return "這是資料夾"
    if isinstance(err, OSError) and err.strerror:
        return err.strerror
    return str(err)


def entry(path, name):
    st = os.lstat(path)
    kind = "dir" if stat.S_ISDIR(st.st_mode) else "link" if stat.S_ISLNK(st.st_mode) else "file"
    item = {"name": name, "type": kind, "size": st.st_size, "mtime": st.st_mtime, "mode": stat.filemode(st.st_mode)}
    if kind == "link":
        try:
            item["target_dir"] = os.path.isdir(path)
            item["link"] = os.readlink(path)
        except OSError:
            item["target_dir"] = False
    return item


def cmd_list(path):
    real = os.path.realpath(path)
    names = os.listdir(real)
    entries = []
    for name in names:
        try:
            entries.append(entry(os.path.join(real, name), name))
        except OSError:
            continue
    entries.sort(key=lambda e: (not (e["type"] == "dir" or e.get("target_dir")), e["name"].lower()))
    out({"path": real, "parent": os.path.dirname(real) if real != "/" else None,
         "writable": os.access(real, os.W_OK), "entries": entries})


def cmd_read(path):
    size = os.path.getsize(path)
    if size > TEXT_LIMIT:
        fail(f"檔案太大（超過 {TEXT_LIMIT // 1024 // 1024} MB），請下載後編輯")
    with open(path, "rb") as f:
        data = f.read()
    if b"\0" in data:
        fail("這不是文字檔，無法在網頁上編輯")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        fail("檔案不是 UTF-8 編碼，無法在網頁上編輯")
    out({"content": text, "size": size, "mtime": os.path.getmtime(path), "writable": os.access(path, os.W_OK)})


def cmd_write(path):
    data = sys.stdin.buffer.read()
    folder, name = os.path.split(os.path.abspath(path))
    tmp = os.path.join(folder, f".{name}.portal-tmp")
    mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else None
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)
    out({"ok": True, "size": len(data)})


def cmd_append(part, offset):
    offset = int(offset)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if offset == 0 else 0)
    fd = os.open(part, flags, 0o644)
    try:
        if os.fstat(fd).st_size != offset:
            fail("上傳片段順序不正確，請重新上傳")
        os.lseek(fd, offset, os.SEEK_SET)
        data = sys.stdin.buffer.read()
        os.write(fd, data)
        size = os.fstat(fd).st_size
    finally:
        os.close(fd)
    out({"size": size})


def cmd_finish(part, dest, overwrite):
    if os.path.lexists(dest) and overwrite != "1":
        fail("已經有同名的檔案")
    os.replace(part, dest)
    out({"ok": True})


def cmd_abort(part):
    try:
        os.unlink(part)
    except FileNotFoundError:
        pass
    out({"ok": True})


def cmd_mkdir(path):
    os.mkdir(path)
    out({"ok": True})


def cmd_rename(src, dst):
    if os.path.lexists(dst):
        fail("已經有同名的檔案或資料夾")
    os.rename(src, dst)
    out({"ok": True})


def cmd_delete(*paths):
    for path in paths:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    out({"ok": True})


def walk(paths):
    """Yield (absolute path, archive name, size) for every file below paths."""
    for path in paths:
        base = os.path.dirname(os.path.abspath(path))
        if os.path.isdir(path) and not os.path.islink(path):
            for root, dirs, files in os.walk(path, onerror=lambda e: None):
                dirs.sort()
                for name in sorted(files):
                    full = os.path.join(root, name)
                    try:
                        size = os.lstat(full).st_size
                    except OSError:
                        continue
                    yield full, os.path.relpath(full, base), size
                if not files and not dirs:
                    yield root, os.path.relpath(root, base) + "/", 0
        else:
            try:
                yield path, os.path.basename(path), os.lstat(path).st_size
            except OSError:
                continue


def cmd_size(*paths):
    total = count = 0
    for _, _, size in walk(paths):
        total += size
        count += 1
    out({"bytes": total, "files": count})


def cmd_stat(path):
    st = os.stat(path)
    if stat.S_ISDIR(st.st_mode):
        fail("這是資料夾")
    out({"size": st.st_size, "readable": os.access(path, os.R_OK)})


def cmd_cat(path):
    with open(path, "rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer, 1024 * 1024)


def cmd_zip(total, *paths):
    total = int(total)
    done, skipped = 0, []
    last = 0.0
    zf = zipfile.ZipFile(sys.stdout.buffer, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
    def report(force=False):
        nonlocal last
        now = time.time()
        if force or now - last > 0.3:
            sys.stderr.write(f"P {done} {total}\n")
            sys.stderr.flush()
            last = now

    for full, arc, size in walk(paths):
        start = done
        try:
            if arc.endswith("/"):
                zf.writestr(arc, b"")
            elif os.path.islink(full):
                zf.writestr(arc, os.readlink(full))
            else:
                # Copy in 1 MB pieces so progress also moves inside large files.
                info = zipfile.ZipInfo.from_file(full, arc)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as src, zf.open(info, "w", force_zip64=size > 2**31 - 2**20) as dst:
                    while True:
                        buf = src.read(1024 * 1024)
                        if not buf:
                            break
                        dst.write(buf)
                        done += len(buf)
                        report()
        except OSError as err:
            skipped.append(f"{arc}：{describe(err)}")
        done = start + size
        report()
    if skipped:
        zf.writestr("_無法讀取的檔案.txt", "以下檔案因權限或讀取錯誤沒有被壓縮：\n" + "\n".join(skipped) + "\n")
    zf.close()
    sys.stderr.write(f"P {total} {total}\n")


COMMANDS = {
    "list": cmd_list, "read": cmd_read, "write": cmd_write, "append": cmd_append, "finish": cmd_finish,
    "abort": cmd_abort, "mkdir": cmd_mkdir, "rename": cmd_rename, "delete": cmd_delete, "size": cmd_size,
    "stat": cmd_stat, "cat": cmd_cat, "zip": cmd_zip,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        fail("unknown command")
    try:
        COMMANDS[sys.argv[1]](*sys.argv[2:])
    except SystemExit:
        raise
    except BrokenPipeError:
        sys.exit(1)
    except Exception as err:  # noqa: BLE001 - report every failure to the caller
        fail(describe(err))
