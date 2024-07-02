"""
Code related to the prompt (in the sense of "directly ask the user a question")
"""

from typing import Optional, Tuple, Any
import time
import re
from pathlib import Path
import json
from textwrap import dedent
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.completion import WordCompleter

from .misc import cache_dir
from .logger import whi, red, md_printer

def get_toolbar_text(settings: dict) -> Any:
    "parsed settings to be well displayed in the prompt toolbar"
    out = []
    keys = sorted(list(settings.keys()))
    for k in keys:
        if k == "task":
            continue
        v = settings[k]
        out.append(f"{k.replace('_', ' - ').title()}: {v}")
    out = ['class:toolbar'] + [" - ".join(out)]
    return FormattedText([tuple(out)])


class SettingsCompleter(Completer):
    def __init__(
        self,
        doctoolsCliSettings,
        doctoolsHistoryPrompts,
        doctoolsHistoryWords,
        *args,
        **kwargs):
        super().__init__(*args, **kwargs)
        self.doctoolsCliSettings = doctoolsCliSettings
        self.doctoolsHistoryPrompts = doctoolsHistoryPrompts
        self.doctoolsHistoryWords = doctoolsHistoryWords

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.strip():
            yield Completion("/debug", start_position=-len(text))
            yield Completion("/settings", start_position=-len(text))
            yield Completion("/reset_memory", start_position=-len(text))
            yield Completion("/help", start_position=-len(text))
        elif text.startswith("/"):
            if "/debug".startswith(text):
                yield Completion("/debug", start_position=-len(text))
            if "/reset_memory".startswith(text):
                yield Completion("/reset_memory", start_position=-len(text))
            if "/help".startswith(text):
                yield Completion("/help", start_position=-len(text))
            if "/settings ".startswith(text) or "/settings " in text:
                settings = sorted(list(self.doctoolsCliSettings.keys()))
                for setting in settings:
                    if setting == "task":
                        continue
                    compl = f"/settings {setting}={self.doctoolsCliSettings[setting]}"
                    if compl.startswith(text):
                        yield Completion(compl, start_position=-len(text))
        else:
            # words autocompletion
            if " " in text and not text.endswith(" "):
                last_word = text.split(" ")[-1]
                word_cnt = 0
                for word in self.doctoolsHistoryWords:
                    if word_cnt >= 3:
                        break
                    if word.lower().startswith(last_word.lower()):
                        yield Completion(word, start_position=-len(last_word))
                        word_cnt += 1

            # entire prompt autocompletion
            for hist in self.doctoolsHistoryPrompts:
                if hist.lower().startswith(text.lower()):
                    yield Completion(hist, start_position=-len(text))

def show_help() -> None:
    "display CLI help"
    md_printer(dedent(ask_user.__doc__).strip())

def ask_user(settings: dict) -> Tuple[str, dict]:
    """
    ## Command line manual
    * **Available Commands:**
        * /help or ?
        * /debug
        * /settings (syntax: '/settings top_k=5')
        * /reset_memory  (to reset the conversation)
    * **Settings keys and values:**
        * top_k: int > 0
        * multiline: boolean
        *retriever: a string containing '_' separated retriever from the
        following list:
            * 'default' to use regular embedding search
            * 'knn' to use KNN
            * 'svm' to use SVM
            * 'hyde' to use Hypothetical Document Embedding search
            * 'parent' to use parent retriever
        To use several '/settings retriever=knn_svm_default'
        * relevancy: float, from set ]0:1]
    * **Tips:**
        * In multiline mode, use ctrl+D to send the text (sometimes
        multiple times).
        * For more information: 'python DocToolsLLM.py --help'
        * History is saved and shared across all runs
        * If you use '>>>>' once in the middle of your text, the left part will be
        used as a query find the documents and the right part will be the
        question to answer. For example: 'tuberculosis among medical students
        in the 20th century >>>> what are the statistics about epidemiology
        of tuberculosis among medical students in the 20th century?'. This is
        not always useful but in some cases depending on documents and
        retriever it can be needed to avoid having to set top_k too high.
    """
    md_printer("# DocToolsLLM Prompt")

    # loading history from files
    prev_questions = []
    pp_file = cache_dir / "query_history.json"
    if pp_file.exists():
        pp_list = json.load(pp_file.open("r"))
        assert isinstance(pp_list, list), "Invalid cache type"
        for i, pp in enumerate(pp_list):
            assert isinstance(pp, dict), "Invalid item in cache"
            assert "prompt" in pp, "Invalid item in cache"
        for pp in pp_list:
            if "timestamp" not in pp:
                pp["timestamp"] = 0
            if "task" not in pp:
                pp["task"] = "query"
            if pp not in prev_questions:
                prev_questions.append(pp)
        prev_questions = sorted(
                prev_questions,
                key=lambda x: x["timestamp"],
                )

    prompts = [x["prompt"] for x in prev_questions if x["task"] == settings["task"]]
    words = [w for w in " ".join(prompts).split(" ") if len(w) > 2 and w.isalpha()]
    completer=SettingsCompleter(
        doctoolsCliSettings=settings,
        doctoolsHistoryPrompts=prompts,
        doctoolsHistoryWords=words
    )

    while True:
        if settings["multiline"]:
            whi("Multiline mode enabled. Use ctrl+D to send.")
        session = PromptSession(
            bottom_toolbar=lambda: get_toolbar_text(settings),
            completer=completer,
            )
        try:
            user_input = session.prompt(
                "> ",
                #completer=autocomplete,
                vi_mode=True,
                multiline=settings["multiline"],
            )
        except KeyboardInterrupt:
            red(f"Quitting.")
            raise SystemExit()
        except EOFError:
            if settings["multiline"]:
                pass
            else:
                red(f"Quitting.")
                raise SystemExit()
        user_input = user_input.strip()

        # quit
        if user_input.strip() in ["quit", "Q", "q"]:
            whi("Quitting.")
            raise SystemExit()
        elif user_input == "/debug":
            whi("Entering debug mode.")
            breakpoint()
            whi("Going back to the prompt.")
            continue
        elif user_input == "/reset_memory":
            whi("Reseting memory.")
            # actually the memory will be reset once we return to the DocToolsLLM instance
            settings["do_reset_memory"] = True
            continue
        elif user_input in ["/help", "?"]:
            show_help()
            continue

        # handle settings
        if user_input.startswith("/settings "):
            if "=" not in user_input:
                red("Invalid settings syntax: missing '='")
                show_help()
                continue
            input_sett = user_input.split(" ")
            if not input_sett[0] == "/settings":
                red("Invalid settings syntax: does not start with '/settings '")
                show_help()
                continue
            if not len(input_sett) == 2:
                red("Invalid settings syntax: too many spaces")
                show_help()
                continue
            input_sett = input_sett[1]
            input_sett = input_sett.split("=")
            if not len(input_sett) == 2:
                red("Invalid settings syntax: expected one '=' symbol")
                show_help()
                continue
            sett_k, sett_v = input_sett
            if sett_k not in settings.keys():
                red("Invalid settings: '{sett_k}' is not a valid setting key")
                show_help()
                continue
            if settings[sett_k] == sett_v:
                red("Invalid settings: '{sett_k}' is already has value '{sett_v}'")
                show_help()
                continue
            try:
                if sett_k == "top_k":
                    assert int(sett_v) > 0, f"Can't set top_k to <= 0 ({sett_v})"
                elif sett_k == "relevancy":
                    assert float(sett_v) > 0 and float(sett_v) <= 1, f"Can't set relevancy to <= 0 or >1 ({sett_v})"
                elif sett_k == "retriever":
                    assert all(
                        retriev in ["default", "hyde", "knn", "svm", "parent"]
                        for retriev in sett_v.split("_")
                    ), f"Invalid retriever value: {sett_v}"
                elif sett_k == "multiline":
                    if sett_v.lower() == "true":
                        sett_v = True
                    elif sett_v.lower() == "false":
                        sett_v = False
                    sett_v = bool(sett_v)
                settings[sett_k] = type(settings[sett_k])(sett_v)
            except Exception as err:
                red(f"Error: can't set '{sett_k}' to '{sett_v}' because it "
                    f"can't keep the type '{type(settings[sett_k])}'\n"
                    f"Error: '{err}'")
                show_help()
                continue
            whi(f"Set {sett_k}={sett_v}")
            continue
        elif "/settings" in user_input:
            red(f"Detected '/settings' but not at the start, retrying.")
            show_help()
            continue

        break
    md_printer("### Done prompting")

    # saving new history to file
    if len(
        [
            x
            for x in prev_questions
            if x["prompt"].strip() == user_input
        ]) == 0:
        prev_questions.append(
                {
                    "prompt": user_input,
                    "timestamp": int(time.time()),
                    "task": settings["task"],
                    })
    prev_questions = sorted(
            prev_questions,
            key=lambda x: x["timestamp"],
            )
    temp_file = Path(str(pp_file.resolve().absolute()) + ".temp")
    json.dump(prev_questions, temp_file.open("w"), indent=4)
    assert temp_file.exists()
    pp_file.unlink(missing_ok=True)
    temp_file.rename(pp_file)

    return user_input, settings
