"""Agent 删除守卫:不让执行引擎删掉数据目录、项目目录本身或它们的上级目录。

事故(2026-10-22,用户机器):pi 引擎把路径里的 @videoagents 手误写成 @videoAgents,APFS 不分大小写,
文件其实写进了真目录,write 工具回执却原样回显错的写法;模型以为自己多建了一棵「重复目录树」,随即
`rm -rf '…/Application Support/@videoAgents' 2>/dev/null || true`,桌面端整个数据目录(全部项目、
Python 运行时、登录凭据)被删。

判据一律按 (st_dev, st_ino) 比对,不比字符串:大小写、符号链接、`..`、结尾斜杠写法不同都认得出是同一目录。
受保护 = VIDEOAGENTS_GUARD_ROOTS 里每个目录连同其各级上级目录,外加 VIDEOAGENTS_GUARD_CONTAINERS
(projects/)的直接子目录(各项目根)。项目里的文件、子目录照常可删。

只用标准库,宿主以 `python -I -S` 起,不 import 仓库其它模块。入口:
- `rm <真 rm> <参数…>`:PATH 最前的 rm 垫片(core.delete_guard_bin_dir 生成)在操作数里有目录时转进来,
  此时变量、通配符、cd 都已由 shell 展开,判断最准;命中即整条拒绝(什么都不删),否则 exec 真 rm;
- `hook`:pi 扩展(pi_delete_guard.mjs)每次工具调用前经 stdin 送 {tool, input, cwd},返回
  {block, reason}(bash / powershell 里字面写出的删除)或 {path}(把路径纠正成磁盘上的真实大小写)。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import stat
import sys
import unicodedata

ENV_ROOTS = "VIDEOAGENTS_GUARD_ROOTS"            # os.pathsep 分隔
ENV_CONTAINERS = "VIDEOAGENTS_GUARD_CONTAINERS"  # os.pathsep 分隔;其直接子目录受保护
TAG = "[VideoAgents 删除守卫]"
PATH_TOOLS = ("read", "write", "edit", "ls", "find", "grep")   # pi 内置工具里带 path 参数的

# find 带这些条件时视为按条件挑着删,不当成清空整个目录
_FIND_FILTERS = {"-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
                 "-lname", "-ilname", "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin",
                 "-size", "-perm", "-user", "-group", "-links", "-inum", "-samefile", "-empty"}
_WRAPPERS = {"sudo", "doas", "command", "builtin", "exec", "nohup", "time", "nice", "env"}
_SEPARATORS = {";", "&&", "||", "|", "&", "|&", ";;", ";&", ";;&", "(", ")"}
_GLOB_ALL = {"*", ".*", "{*,.*}", "{.*,*}"}
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_RMTREE = re.compile(r"\brmtree\(\s*[rRuU]?(['\"])([^'\"\n]+)\1")
_PS_DELETE = {"remove-item", "rm", "ri", "del", "erase", "rd", "rmdir"}
_PS_VALUE_PARAMS = {"-filter", "-include", "-exclude", "-credential", "-stream"}


def _split_env(name: str) -> list[str]:
    return [p for p in (os.environ.get(name) or "").split(os.pathsep) if p]


def _stat(path: str, follow: bool = True):
    try:
        return os.stat(path) if follow else os.lstat(path)
    except (OSError, ValueError):
        return None


def _abs(path: str, cwd: str) -> str:
    return os.path.abspath(os.path.join(cwd, os.path.expanduser(path)))


def true_case(path: str) -> str:
    """把已存在的每一级换成磁盘上的真实写法(只在该写法确实解析到某个已有条目、即文件系统不分大小写 /
    不分 Unicode 规范形式时才换);不存在的部分原样保留。"""
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    parts = [p for p in re.split(r"[\\/]+", rest) if p]
    cur = drive + os.sep
    for i, comp in enumerate(parts):
        cand = os.path.join(cur, comp)
        try:
            names = os.listdir(cur) if os.path.lexists(cand) else None
        except OSError:
            names = None
        if names is None:
            return os.path.join(cur, *parts[i:])
        if comp not in names:
            want = unicodedata.normalize("NFC", comp).casefold()
            hits = [n for n in names if unicodedata.normalize("NFC", n).casefold() == want]
            if len(hits) == 1:
                comp = hits[0]
        cur = os.path.join(cur, comp)
    return cur


def fix_path(path: str, cwd: str) -> str | None:
    """工具参数里的路径与磁盘真实大小写不一致时返回纠正后的绝对路径,否则 None。"""
    full = _abs(path, cwd)
    fixed = true_case(full)
    return fixed if fixed != full else None


def rm_operands(args: list[str]) -> list[str]:
    ops, opts_done = [], False
    for a in args:
        if not opts_done and a == "--":
            opts_done = True
        elif opts_done or not a.startswith("-") or a == "-":
            ops.append(a)
    return ops


class Guard:
    def __init__(self, roots: list[str], containers: list[str]):
        self.protected: set[tuple[int, int]] = set()
        for root in [*roots, *containers]:   # 容器本身(projects/)同样受保护
            p = os.path.abspath(os.path.expanduser(root))
            while True:
                st = _stat(p)
                if st and stat.S_ISDIR(st.st_mode):
                    self.protected.add((st.st_dev, st.st_ino))
                parent = os.path.dirname(p)
                if parent == p:
                    break
                p = parent
        self.containers: set[tuple[int, int]] = set()
        for c in containers:
            st = _stat(os.path.abspath(os.path.expanduser(c)))
            if st and stat.S_ISDIR(st.st_mode):
                self.containers.add((st.st_dev, st.st_ino))

    @classmethod
    def from_env(cls) -> "Guard":
        return cls(_split_env(ENV_ROOTS), _split_env(ENV_CONTAINERS))

    def __bool__(self) -> bool:
        return bool(self.protected or self.containers)

    def is_protected(self, path: str, cwd: str) -> bool:
        """path 是受保护目录本身。不存在、普通文件、符号链接本身(rm 只删链接)都不算;
        结尾带 / 时 rm 会跟随链接删其指向目录的内容,按指向判断。"""
        full = os.path.join(cwd, os.path.expanduser(path))
        st = _stat(full, follow=path.endswith(("/", "/.", "\\")))
        if not st or not stat.S_ISDIR(st.st_mode):
            return False
        if (st.st_dev, st.st_ino) in self.protected:
            return True
        parent = _stat(os.path.dirname(_abs(path, cwd)))
        return bool(parent) and (parent.st_dev, parent.st_ino) in self.containers

    def _clears(self, operands: list[str], cwd: str) -> str | None:
        """操作数合起来是某个受保护目录下的全部(非隐藏)条目(`rm -rf 项目/*` 展开后的样子)时返回该目录。"""
        by_parent: dict[str, set[str]] = {}
        for op in operands:
            full = _abs(op, cwd)
            by_parent.setdefault(os.path.dirname(full), set()).add(os.path.basename(full).casefold())
        for parent, names in by_parent.items():
            if not self.is_protected(parent, "/"):
                continue
            try:
                entries = {e.casefold() for e in os.listdir(parent) if not e.startswith(".")}
            except OSError:
                continue
            if len(entries) >= 2 and entries <= names:
                return parent
        return None

    def check_delete(self, operands: list[str], cwd: str) -> str | None:
        """删除这些操作数会动到受保护目录时返回拒绝说明。"""
        for op in operands:
            if self.is_protected(op, cwd):
                return blocked_message(_abs(op, cwd))
        parent = self._clears(operands, cwd)
        return blocked_message(parent, cleared=True) if parent else None

    def _check_glob(self, op: str, cwd: str) -> str | None:
        """字面写出的 `目录/*`:目录受保护即拒绝;其它通配交给运行时的 rm 垫片(届时已展开)。"""
        head, tail = os.path.split(op)
        if tail in _GLOB_ALL and not any(c in head for c in "*?[{$`"):
            if self.is_protected(head or ".", cwd):
                return blocked_message(_abs(head or ".", cwd), cleared=True)
        return None

    def _check_simple(self, words: list[str], cwd: str) -> tuple[str | None, str]:
        """一条简单命令;返回 (拒绝说明, 执行后的 cwd)。"""
        i = 0
        while i < len(words) and re.match(r"[A-Za-z_][A-Za-z0-9_]*=", words[i]):
            i += 1
        while i < len(words) and os.path.basename(words[i]) in _WRAPPERS:
            env_like = os.path.basename(words[i]) == "env"
            i += 1
            while i < len(words) and (words[i].startswith("-") or (env_like and "=" in words[i])):
                i += 1
        words = words[i:]
        if not words:
            return None, cwd
        name, args = os.path.basename(words[0]), words[1:]
        if name == "cd":
            target = args[0] if args else "~"
            if target != "-" and not any(c in target for c in "$`"):
                cwd = _abs(target, cwd)
            return None, cwd
        if name == "rm":
            literal = []
            for op in rm_operands(args):
                if any(c in op for c in "*?[{"):
                    msg = self._check_glob(op, cwd)
                    if msg:
                        return msg, cwd
                elif not any(c in op for c in "$`"):
                    literal.append(op)
            return self.check_delete(literal, cwd), cwd
        if name == "find":
            starts = []
            for a in args:
                if a.startswith(("-", "(", "!")):
                    break
                starts.append(a)
            deletes = "-delete" in args or any(
                a in ("-exec", "-execdir", "-ok", "-okdir") and j + 1 < len(args)
                and os.path.basename(args[j + 1]) in ("rm", "rmdir") for j, a in enumerate(args))
            if deletes and not _FIND_FILTERS.intersection(args):
                for s in starts:
                    if not any(c in s for c in "$`*?[") and self.is_protected(s, cwd):
                        return blocked_message(_abs(s, cwd), cleared=True), cwd
        return None, cwd

    def check_bash(self, command: str, cwd: str) -> str | None:
        """bash 命令里字面写出的删除(含 /bin/rm 绝对路径、find -delete、Python rmtree)。
        变量、命令替换等解析不了的留给运行时的 rm 垫片;解析失败的行跳过,不误拦。"""
        for m in _RMTREE.finditer(command):
            if self.is_protected(m.group(2), cwd):
                return blocked_message(_abs(m.group(2), cwd))
        lines, heredocs, buf = [], [], ""
        for raw in command.splitlines():
            if heredocs:
                if raw.strip() == heredocs[0]:
                    heredocs.pop(0)
                continue
            heredocs = [m.group(2) for m in _HEREDOC.finditer(raw)]
            if raw.endswith("\\"):
                buf += raw[:-1] + " "
                continue
            lines.append(buf + raw)
            buf = ""
        if buf:
            lines.append(buf)
        for line in lines:
            try:
                lex = shlex.shlex(line, posix=True, punctuation_chars=True)
                lex.whitespace_split = True
                tokens = list(lex)
            except ValueError:
                continue
            words, skip = [], False
            for tok in tokens + [";"]:
                if skip:
                    skip = False
                    continue
                if tok in _SEPARATORS:
                    msg, cwd = self._check_simple(words, cwd)
                    if msg:
                        return msg
                    words = []
                elif tok and set(tok) <= set("<>&|") and ("<" in tok or ">" in tok):
                    skip = True      # 重定向及其目标
                    if words and words[-1].isdigit():
                        words.pop()  # 2>/dev/null 的 2
                else:
                    words.append(tok)
        return None

    def check_powershell(self, command: str, cwd: str) -> str | None:
        """powershell 工具(Windows 无 Git Bash 时)里字面写出的 Remove-Item / rd / del 等。"""
        for seg in re.split(r"[;|\n]", command):
            try:
                lex = shlex.shlex(seg, posix=False)
                lex.whitespace_split = True
                words = [w.strip("'\"") for w in lex]
            except ValueError:
                continue
            if not words or words[0].lower() not in _PS_DELETE:
                continue
            skip = False
            for w in words[1:]:
                if skip:
                    skip = False
                    continue
                low = w.lower()
                if low in _PS_VALUE_PARAMS:
                    skip = True
                    continue
                if low.startswith("-") or re.fullmatch(r"/[a-z]", low):   # 开关参数 / cmd 风格 /s /q
                    continue
                if any(c in w for c in "$`*?"):
                    continue
                if self.is_protected(w, cwd):
                    return blocked_message(_abs(w, cwd))
        return None


def blocked_message(path: str, cleared: bool = False) -> str:
    real = true_case(path)
    what = f"清空 {real}" if cleared else f"删除 {real}"
    same = (f"注意:本机文件系统不区分大小写,{path} 就是 {real},并不存在需要清理的「重复目录」。"
            if real != path else "")
    return (f"{TAG} 已拒绝{what}:这是 VideoAgents 数据目录 / 项目目录本身或其上级目录,删掉会丢失全部项目。"
            f"只能删除项目里具体的文件或子目录;不要改用别的命令或脚本重试。{same}\n"
            f"Blocked: {real} is a protected VideoAgents directory (the data or project folder itself, or one of "
            f"its parents). Delete only specific files or subfolders inside a project; do not retry another way.")


def hook(req: dict) -> dict:
    tool, inp, cwd = req.get("tool"), req.get("input") or {}, req.get("cwd") or os.getcwd()
    guard = Guard.from_env()
    if tool == "bash" and isinstance(inp.get("command"), str):
        msg = guard.check_bash(inp["command"], cwd) if guard else None
    elif tool == "powershell" and isinstance(inp.get("command"), str):
        msg = guard.check_powershell(inp["command"], cwd) if guard else None
    elif tool in PATH_TOOLS and isinstance(inp.get("path"), str) and inp["path"]:
        fixed = fix_path(inp["path"], cwd)
        return {"path": fixed} if fixed else {}
    else:
        msg = None
    return {"block": True, "reason": msg} if msg else {}


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "rm":
        real, args = argv[1], argv[2:]
        guard = Guard.from_env()
        msg = guard.check_delete(rm_operands(args), os.getcwd()) if guard else None
        if msg:
            sys.stderr.write(msg + "\n")
            return 1
        os.execv(real, ["rm", *args])
    if argv[:1] == ["hook"]:
        sys.stdout.write(json.dumps(hook(json.load(sys.stdin)), ensure_ascii=False))
        return 0
    sys.stderr.write("usage: delete_guard.py rm <real-rm> [args…] | hook\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
