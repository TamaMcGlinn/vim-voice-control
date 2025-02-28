# Copyright (c) 2022 Antoine Pinsard
import re
import time
from pathlib import Path

import pynvim
import speech_recognition as sr
import yaml

BASE_DIR = Path(__file__).parents[2]
DATA_DIR = BASE_DIR / "data"


class InvalidCommand(Exception):
    def __init__(self, cmd):
        self.cmd = cmd

    def __str__(self):
        return f"Invalid command: {self.cmd}"


class NvimAPI:

    REG_UNNAMED = '"'
    REG_SMALL_DELETE = "-"
    REG_COMMAND = ":"
    REG_INSERT = "."
    REG_FILE = "%"
    REG_ALTERNATE_BUFFER = "#"
    REG_EXPRESSION = "="
    REG_GUI_SELECTION = "*"
    REG_GUI_CLIPBOARD = "+"
    REG_BLACK_HOLE = "_"
    REG_SEARCH = "/"

    def __init__(self, nvim):
        self.nvim = nvim

    def info(self, msg):
        self.nvim.out_write(msg + "\n")

    def error(self, msg):
        self.nvim.err_write(msg + "\n")

    def goto_line(self, lineno):
        self.nvim.feedkeys(f"{lineno}gg")

    def select_lines(self, start, end=None):
        start = int(start)
        keys = f"{start}ggV"
        if end is not None:
            end = int(end)
            keys += f"{end}gg"
        self.nvim.feedkeys(keys)

    def next_tab(self):
        self.nvim.feedkeys("gt")

    def prev_tab(self):
        self.nvim.feedkeys("gT")

    def copy_to_register(self, register=None):
        if register is None:
            self.nvim.feedkeys("y")
        else:
            self.nvim.feedkeys(f'"{register}y')

    def paste_from_register(self, register=None, times=None):
        keys = "p"
        if register is not None:
            keys = f'"{register}{keys}'
        if times is not None:
            keys = f"{times}{keys}"
        self.nvim.feedkeys(keys)


REGISTERS_ALIASES = {
    "main": NvimAPI.REG_UNNAMED,
    "unnamed": NvimAPI.REG_UNNAMED,
    "insert": NvimAPI.REG_INSERT,
    "file": NvimAPI.REG_FILE,
    "path": NvimAPI.REG_FILE,
    "current file": NvimAPI.REG_FILE,
    "current path": NvimAPI.REG_FILE,
    "command": NvimAPI.REG_COMMAND,
    "command line": NvimAPI.REG_COMMAND,
    "last command": NvimAPI.REG_COMMAND,
    "last command line": NvimAPI.REG_COMMAND,
    "alternate file": NvimAPI.REG_ALTERNATE_BUFFER,
    "alternate buffer": NvimAPI.REG_ALTERNATE_BUFFER,
    "expression": NvimAPI.REG_EXPRESSION,
    "last expression": NvimAPI.REG_EXPRESSION,
    "search": NvimAPI.REG_SEARCH,
    "last search": NvimAPI.REG_SEARCH,
    "delete": NvimAPI.REG_SMALL_DELETE,
    "small delete": NvimAPI.REG_SMALL_DELETE,
    "selection": NvimAPI.REG_GUI_SELECTION,
    "clipboard": NvimAPI.REG_GUI_CLIPBOARD,
    "black hole": NvimAPI.REG_BLACK_HOLE,
    "void": NvimAPI.REG_BLACK_HOLE,
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eigh": "8",
    "nine": "9",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "nineth": "9",
}


TYPE_TO_REGEX = {
    "UINT": r"\d+",
    "INT": r"-?\d+",
    "ID": ".+",
    "PATH": ".+",
    "REG": ".+",
}


TYPE_CONVERSION = {
    "UINT": int,
    "INT": int,
    "ID": lambda x: re.sub("\s+", "_", x),
    "PATH": lambda x: re.sub("\s+", "/", x),
    "REG": lambda x: REGISTERS_ALIASES.get(x.lower(), x)[0],
}


def param_to_regex(i, argtypes):
    def replacer(m):
        arg_name = m[1]
        regex = TYPE_TO_REGEX[argtypes[arg_name]]
        return f"(?P<{arg_name}_{i}>{regex})"

    return replacer


@pynvim.plugin
class VoiceControl(NvimAPI):

    NOISE_DETECTION_INTERVAL = 30 * 60

    def __init__(self, nvim):
        super().__init__(nvim)
        self.recognizer = sr.Recognizer()
        self.mic = sr.Microphone()
        self.last_noise_detection = 0
        self.re_commands = self.get_re_commands()

    def get_re_commands(self):
        with open(DATA_DIR / "cmd-to-nl.yml") as f:
            cmd_to_nl = yaml.load(f, Loader=yaml.Loader)
        variables = cmd_to_nl.pop("vars", {})
        for name, values in variables.items():
            variables[name] = "(" + "|".join(values) + ")"
        re_commands = []
        for cmd, nl_cmds in cmd_to_nl.items():
            cmd = cmd.split()
            cmd_name = cmd[0]
            cmd_args = []
            argtypes = {}
            for arg in cmd[1:]:
                if m := re.fullmatch(r"([A-Z]+)\(([a-z])\)", arg):
                    arg_type = m[1]
                    arg_name = m[2]
                    argtypes[arg_name] = arg_type
                    cmd_args.append((arg_name, TYPE_CONVERSION[arg_type]))
                elif m := re.fullmatch(r"([A-Z]+)\((.*?)\)", arg):
                    cmd_args.append((None, TYPE_CONVERSION[m[1]](m[2])))
                else:
                    raise SyntaxError("Invalid command definition: {cmd}")
            parts = []
            for i, nl_cmd in enumerate(nl_cmds):
                part = re.sub(r"\b[A-Z_]+\b", lambda m: variables[m[0]], nl_cmd)
                part = re.sub(r"\{([a-z])\}", param_to_regex(i, argtypes), part)
                parts.append(part)
            re_commands.append(
                ([cmd_name, *cmd_args], re.compile("(" + "|".join(parts) + ")", re.I))
            )
        return re_commands

    def check_noise(self, source):
        if time.time() - self.last_noise_detection > self.NOISE_DETECTION_INTERVAL:
            self.info("Noise detection. Keep quiet...")
            self.recognizer.adjust_for_ambient_noise(source)
            self.last_noise_detection = time.time()

    def listen(self, timeout=5, phrase_time_limit=60):
        with self.mic as source:
            self.check_noise(source)
            self.recognizer.adjust_for_ambient_noise(source)
            self.info("Listening...")
            try:
                audio = self.recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit,
                )
            except sr.WaitTimeoutError:
                self.error("I didn't hear what you said.")
                return None
            else:
                self.info("Analyzing your voice command...")
                transcript = self.recognizer.recognize_google(audio)
                self.info(f"\U0001f5e3  « {transcript} »")
                return transcript

    def regex_parse_command(self, nl_cmd):
        for cmd, regex in self.re_commands:
            if m := regex.fullmatch(nl_cmd):
                kwargs = {k[0]: v for k, v in m.groupdict().items() if v is not None}
                args = []
                for arg_name, arg_type in cmd[1:]:
                    if arg_name is None:
                        args.append(arg_type)
                    else:
                        args.append(arg_type(kwargs[arg_name]))
                return [cmd[0], *args]
        raise InvalidCommand(nl_cmd)

    def neural_parse_command(self, nl_cmd):
        raise InvalidCommand(nl_cmd)

    def parse_command(self, nl_cmd):
        try:
            return self.neural_parse_command(nl_cmd)
        except InvalidCommand:
            return self.regex_parse_command(nl_cmd)

    def execute_command(self, cmd, *args):
        cmd = cmd.lower()
        handler_names = [f"cmd_{cmd}", cmd]
        for handler_name in handler_names:
            try:
                handler = getattr(self, handler_name)
            except AttributeError:
                continue
            else:
                return handler(*args)
        raise NotImplementedError(f"This command is not yet implemented: {cmd} {args}")

    @pynvim.command("Vvc")
    def voice_command(self, timeout=5):
        nl_cmd = self.listen(timeout=timeout)
        if nl_cmd is None:
            return False
        try:
            cmd = self.parse_command(nl_cmd)
        except InvalidCommand as e:
            self.error(str(e))
            return False
        try:
            self.execute_command(*cmd)
        except NotImplementedError as e:
            self.error(str(e))
            return False
        else:
            return True

    @pynvim.command("VvcMode")
    def voice_command_mode(self):
        while True:
            try:
                if self.voice_command(timeout=None) is False:
                    time.sleep(3)
            except StopIteration:
                break

    def cmd_paste_from_register(self, register=None, times=None):
        if times is None and isinstance(register, int):
            # Allow to pass "times" as first argument when register is not given
            register, times = times, register
        self.paste_from_register(register, times)

    def cmd_smart_complete(self):
        raise NotImplementedError

    def cmd_smart_edit(self):
        raise NotImplementedError

    def cmd_vvc_off(self):
        raise StopIteration
