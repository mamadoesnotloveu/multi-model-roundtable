"""多模型圆桌讨论的核心逻辑。

完整流程：
1. 多轮辩论（自适应收敛）：各模型（运动员）轮流发言、互相点评反驳，观点收敛时提前结束；
2. 起草：起草人（运动员之一）根据讨论起草方案草案；
3. 表决（盖章）：每个运动员对草案给出「同意 / 部分同意 / 不同意」的独立表态；
4. 独立评委最终裁决：评委（不属于运动员）按「少数服从多数」原则做最终裁决。

本文件不依赖 Streamlit，可以单独测试或复用。
"""
from __future__ import annotations

import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

# 各阶段的最大输出 token 数（推理模型会把一部分额度用于“思考”，需留足余量）
MAX_TOKENS = 8192          # 辩论每轮发言
DRAFT_MAX_TOKENS = 16384   # 起草方案草案
VOTE_MAX_TOKENS = 2048     # 表决（模板输出，较短）
FINAL_MAX_TOKENS = 16384   # 评委最终裁决
CONVERGE_MAX_TOKENS = 512  # 收敛判断（只回两个字）
# 讨论温度（越高越发散，越低越保守）
TEMPERATURE = 0.7

# 各平台的默认模型名（可在网页侧边栏修改）
DEFAULT_MODELS = {
    "claude": "claude-sonnet-5",
    "openai": "gpt-5.6-terra",
    "deepseek": "deepseek-v4-pro",
    "grok": "grok-4.6",
}

# 平台显示名
PROVIDER_LABELS = {
    "claude": "Claude",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "grok": "Grok",
}

# OpenAI 兼容接口的自定义 base_url（openai 用官方默认 None）
_OPENAI_COMPAT_BASE = {
    "deepseek": "https://api.deepseek.com",
    "grok": "https://api.x.ai/v1",
}


@dataclass
class ModelConfig:
    """单个参与模型 / 起草人 / 评委的配置。"""
    provider: str      # "claude" | "openai" | "deepseek" | "grok"
    name: str          # 模型名
    api_key: str
    label: str         # 显示名
    base_url: str = ""  # 可选：自定义 API 地址（如中转服务），留空用官方地址


@dataclass
class Utterance:
    """一次辩论发言。"""
    speaker_label: str
    round_no: int
    content: str


@dataclass
class Vote:
    """一次表决（盖章）。"""
    voter_label: str
    content: str


# 辩手（运动员）的系统提示词
DEBATER_SYSTEM = (
    "你是一位圆桌讨论的参与者（运动员），正在与其他几位大模型一起讨论同一个话题。\n"
    "请遵守以下规则：\n"
    "1. 认真阅读已有的讨论记录和参考附件；\n"
    "2. 主动指出他人观点中的漏洞、风险或考虑不周之处，不要一味附和；\n"
    "3. 给出你自己的独立判断、方案或补充；\n"
    "4. 观点要有依据：尽量引用附件或讨论中的具体内容，而非空泛断言；\n"
    "5. 语言简洁、直接、具体，避免空话套话。"
)

# 起草人的系统提示词
DRAFTER_SYSTEM = (
    "你是一位圆桌讨论的起草人（主持人），负责根据多方讨论起草一份结论方案草案。\n"
    "要求：\n"
    "1. 客观综合各方观点，不偏袒任何一方；\n"
    "2. 草案要具体、可执行，结构清晰；\n"
    "3. 对确实存在的分歧，如实在草案中标注出来；\n"
    "4. 关键结论尽量标注依据来源；\n"
    "5. 本阶段只输出方案草案，不要下最终结论。"
)

# 表决（盖章）时的系统提示词
VOTER_SYSTEM = (
    "你是圆桌讨论的成员，现在要对一份方案草案进行表决。\n"
    "要求：\n"
    "1. 认真阅读草案，判断它是否忠实反映讨论、是否可行；\n"
    "2. 明确给出立场：同意 / 部分同意 / 不同意；\n"
    "3. 确有异议请直接提出，不要为了和谐而违心同意；\n"
    "4. 严格按下面的模板输出：\n"
    "立场：同意 / 部分同意 / 不同意\n"
    "理由：（1~2 句）\n"
    "修改建议：（若立场为“同意”，写“无”）"
)

# 独立评委的系统提示词
JUDGE_SYSTEM = (
    "你是一位独立评委/仲裁者，不属于参与讨论的任何一方。\n"
    "你的任务是基于方案草案和各方表决，做出公正的最终裁决。\n"
    "要求：\n"
    "1. 不偏袒任何一方；\n"
    "2. 按「少数服从多数」原则采纳多数意见，同时如实保留少数意见；\n"
    "3. 关键结论要给出依据说明；\n"
    "4. 严格按以下四部分输出，标题用【】括起来：\n"
    "【最终方案】综合各方意见后的最终方案\n"
    "【各方盖章】每个成员的立场与理由\n"
    "【保留分歧】仍无法调和的分歧及各方立场\n"
    "【依据说明】关键结论的主要依据/证据来源"
)

# 收敛判断
CONVERGE_SYSTEM = "你是讨论收敛判断助手。只回复「收敛」或「未收敛」两个字。"


def missing_dependencies() -> list:
    """返回尚未安装的第三方依赖（openai / anthropic）。"""
    missing = []
    for mod in ("openai", "anthropic"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def _build_messages(system: str, user_message: str) -> list:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


def _add_usage(usage, label: str, input_tokens, output_tokens) -> None:
    """把一次调用的 token 用量累加到 usage[label] = {"input": int, "output": int}。"""
    if usage is None:
        return
    u = usage.setdefault(label, {"input": 0, "output": 0})
    try:
        u["input"] += int(input_tokens or 0)
        u["output"] += int(output_tokens or 0)
    except (TypeError, ValueError):
        pass


def call_model(cfg: ModelConfig, system: str, user_message: str, max_tokens: int = MAX_TOKENS, usage: Optional[dict] = None) -> str:
    """调用单个模型，返回其文本回复。失败会抛出异常。usage（可选）用于累加 token 用量。"""
    if cfg.provider == "claude":
        from anthropic import Anthropic

        client = Anthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)
        resp = client.messages.create(
            model=cfg.name,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        parts = []
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                parts.append(block.text)
        if usage is not None:
            u = getattr(resp, "usage", None)
            if u:
                _add_usage(usage, cfg.label, getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0))
        return "".join(parts).strip()

    # OpenAI 兼容：openai / deepseek / grok
    from openai import OpenAI

    base_url = _OPENAI_COMPAT_BASE.get(cfg.provider)  # openai -> None
    client = OpenAI(api_key=cfg.api_key, base_url=base_url)
    messages = _build_messages(system, user_message)

    if cfg.provider == "openai":
        # 新版 OpenAI 推理模型（gpt-5.x）用 max_completion_tokens，且不支持自定义 temperature
        try:
            resp = client.chat.completions.create(
                model=cfg.name,
                max_completion_tokens=max_tokens,
                messages=messages,
            )
        except Exception as exc:
            if "max_completion_tokens" in str(exc) or "max_tokens" in str(exc):
                resp = client.chat.completions.create(
                    model=cfg.name,
                    max_tokens=max_tokens,
                    messages=messages,
                )
            else:
                raise
    else:
        # deepseek / grok：标准 OpenAI 兼容参数
        resp = client.chat.completions.create(
            model=cfg.name,
            temperature=TEMPERATURE,
            max_tokens=max_tokens,
            messages=messages,
        )

    if usage is not None:
        u = getattr(resp, "usage", None)
        if u:
            _add_usage(usage, cfg.label, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
    return (resp.choices[0].message.content or "").strip()


def call_model_stream(cfg: ModelConfig, system: str, user_message: str, max_tokens: int = MAX_TOKENS, usage: Optional[dict] = None) -> Iterator[str]:
    """流式调用单个模型，逐段 yield 文本。usage（可选）用于累加 token 用量。"""
    if cfg.provider == "claude":
        from anthropic import Anthropic

        client = Anthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)
        with client.messages.stream(
            model=cfg.name,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                if text:
                    yield text
            if usage is not None:
                try:
                    final_msg = stream.get_final_message()
                    u = getattr(final_msg, "usage", None)
                    if u:
                        _add_usage(usage, cfg.label, getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0))
                except Exception:
                    pass
        return

    from openai import OpenAI

    base_url = _OPENAI_COMPAT_BASE.get(cfg.provider)
    client = OpenAI(api_key=cfg.api_key, base_url=base_url)
    messages = _build_messages(system, user_message)
    kwargs = dict(model=cfg.name, stream=True, messages=messages)
    if cfg.provider == "openai":
        kwargs["max_completion_tokens"] = max_tokens
        kwargs["stream_options"] = {"include_usage": True}
    else:
        kwargs["temperature"] = TEMPERATURE
        kwargs["max_tokens"] = max_tokens

    resp = client.chat.completions.create(**kwargs)
    last_usage = None
    for chunk in resp:
        if getattr(chunk, "usage", None):
            last_usage = chunk.usage
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
    if usage is not None and last_usage is not None:
        _add_usage(usage, cfg.label, getattr(last_usage, "prompt_tokens", 0), getattr(last_usage, "completion_tokens", 0))


def format_transcript(utterances: list) -> str:
    """把辩论发言列表拼成一段可读的讨论记录文本。"""
    lines = []
    for u in utterances:
        lines.append("【{speaker} · 第 {rnd} 轮】\n{content}".format(
            speaker=u.speaker_label, rnd=u.round_no, content=u.content))
    return "\n\n".join(lines)


def format_votes(votes: list) -> str:
    """把表决结果拼成一段文本。"""
    lines = []
    for v in votes:
        lines.append("【{voter}】\n{content}".format(voter=v.voter_label, content=v.content))
    return "\n\n".join(lines)


def _call_participants_parallel(
    participants: list, system: str, user_message: str, round_no: int, errors: list, usage: Optional[dict] = None,
) -> list:
    """同一轮内并发调用所有参与模型（非流式）。"""
    def worker(cfg: ModelConfig) -> Utterance:
        try:
            content = call_model(cfg, system, user_message, usage=usage)
        except Exception as exc:
            errors.append("{label}（第 {rnd} 轮）调用失败：{exc}".format(
                label=cfg.label, rnd=round_no, exc=exc))
            content = "（本轮调用失败，未产生回复：{exc}）".format(exc=exc)
        return Utterance(cfg.label, round_no, content)

    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        return list(executor.map(worker, participants))


def _call_participants_streaming(
    participants: list, system: str, user_message: str, round_no: int, errors: list,
    on_chunk: Optional[Callable[[str, str], None]], usage: Optional[dict] = None,
) -> list:
    """同一轮内并发流式调用所有参与模型，主线程逐段回调 on_chunk(label, text)。"""
    n = len(participants)
    q = queue.Queue()

    def worker(cfg: ModelConfig) -> None:
        try:
            for text in call_model_stream(cfg, system, user_message, usage=usage):
                q.put((cfg.label, text, False))
        except Exception as exc:
            errors.append("{label}（第 {rnd} 轮）调用失败：{exc}".format(
                label=cfg.label, rnd=round_no, exc=exc))
            q.put((cfg.label, "（本轮调用失败，未产生回复：{exc}）".format(exc=exc), False))
        q.put((cfg.label, "", True))  # 完成标记

    threads = [threading.Thread(target=worker, args=(cfg,)) for cfg in participants]
    for t in threads:
        t.start()

    texts = {cfg.label: "" for cfg in participants}
    done = 0
    while done < n:
        try:
            label, text, is_done = q.get(timeout=0.05)
        except queue.Empty:
            continue
        if is_done:
            done += 1
        else:
            texts[label] += text
            if on_chunk:
                on_chunk(label, text)

    for t in threads:
        t.join()
    return [Utterance(cfg.label, round_no, texts[cfg.label]) for cfg in participants]


def _check_convergence(checker: ModelConfig, context: str, transcript: str, usage: Optional[dict] = None) -> bool:
    """用某个模型判断讨论是否已收敛。"""
    msg = (
        "{context}\n\n以下是讨论记录：\n\n{transcript}\n\n"
        "请判断以上讨论是否已收敛（各方没有新的实质性分歧、观点已趋于一致）。只回复两个字：收敛 或 未收敛"
    ).format(context=context, transcript=transcript)
    try:
        ans = call_model(checker, CONVERGE_SYSTEM, msg, max_tokens=CONVERGE_MAX_TOKENS, usage=usage).strip()
    except Exception:
        return False
    return "收敛" in ans and "未" not in ans


def _tally_votes(votes: list) -> dict:
    """从表决文本里解析立场并计数。"""
    counts = {"同意": 0, "部分同意": 0, "不同意": 0, "未识别": 0}
    for v in votes:
        m = re.search(r"立场[:：]\s*(部分同意|不同意|同意)", v.content)
        if m:
            counts[m.group(1)] += 1
        else:
            counts["未识别"] += 1
    return counts


def _call_with_retry(cfg: ModelConfig, system: str, user_message: str, max_tokens: int, retries: int = 2, usage: Optional[dict] = None) -> str:
    """调用模型；若返回空内容则重试，最后仍为空则抛出异常。"""
    for _ in range(retries + 1):
        out = call_model(cfg, system, user_message, max_tokens=max_tokens, usage=usage).strip()
        if out:
            return out
    raise RuntimeError("连续多次返回空内容")


def run_discussion(
    topic: str,
    participants: list,
    drafter: ModelConfig,
    judge: ModelConfig,
    num_rounds: int,
    event_cb: Optional[Callable[[dict], None]] = None,
    attachment_text: str = "",
) -> dict:
    """执行完整流程：辩论（自适应收敛）→ 起草 → 表决 → 独立评委裁决。

    event_cb（可选）接收事件字典：
        {"type": "status", "msg": str}                进度提示
        {"type": "round_start", "round": int, "total": int, "labels": [str]}  一轮辩论开始
        {"type": "chunk", "label": str, "text": str}  某运动员的流式文本片段
        {"type": "round_end", "round": int}           一轮辩论结束

    返回结构：topic / per_round / rounds_run / converged / transcript /
             draft / votes / vote_tally / final / judge / errors
    """
    errors: list = []
    utterances: list = []
    per_round: list = []
    usage: dict = {}
    start_time = time.time()

    def emit(**ev: dict) -> None:
        if event_cb:
            event_cb(ev)

    # 话题 + 附件内容拼成统一上下文
    context = "话题：{topic}".format(topic=topic)
    if attachment_text:
        context += "\n\n【参考附件内容】\n{attachment}".format(attachment=attachment_text)

    # 1. 多轮辩论（自适应收敛）
    converged = False
    rounds_run = 0
    for rnd in range(1, num_rounds + 1):
        if rnd == 1:
            user_message = (
                "{context}\n\n请给出你对这个话题的初步分析与建议方案，"
                "并说明你的核心依据（尽量引用附件或事实，而非空泛判断）。"
            ).format(context=context)
        else:
            transcript = format_transcript(utterances)
            user_message = (
                "{context}\n\n"
                "以下是目前的讨论记录：\n\n{transcript}\n\n"
                "请基于以上记录，先指出其中存在的问题或不足（给出依据），再给出你的改进意见或补充观点。"
            ).format(context=context, transcript=transcript)

        emit(type="status", msg="第 {rnd}/{total} 轮：几位模型正在同时发言…".format(rnd=rnd, total=num_rounds))
        emit(type="round_start", round=rnd, total=num_rounds, labels=[p.label for p in participants])
        if event_cb:
            round_utterances = _call_participants_streaming(
                participants, DEBATER_SYSTEM, user_message, rnd, errors,
                on_chunk=lambda label, text, rnd=rnd: emit(type="chunk", label=label, text=text),
                usage=usage,
            )
        else:
            round_utterances = _call_participants_parallel(
                participants, DEBATER_SYSTEM, user_message, rnd, errors, usage)
        utterances.extend(round_utterances)
        per_round.append(round_utterances)
        rounds_run = rnd
        emit(type="round_end", round=rnd)
        emit(type="status", msg="第 {rnd}/{total} 轮完成".format(rnd=rnd, total=num_rounds))

        # 从第 2 轮起判断是否收敛
        if rnd >= 2:
            emit(type="status", msg="正在判断讨论是否已收敛…")
            if _check_convergence(drafter, context, format_transcript(utterances), usage):
                converged = True
                emit(type="status", msg="观点已收敛，提前结束辩论。")
                break

    transcript = format_transcript(utterances)

    # 2. 起草方案草案
    emit(type="status", msg="起草人（{label}）正在起草方案草案…".format(label=drafter.label))
    draft_message = (
        "{context}\n\n"
        "以下是完整的讨论记录：\n\n{transcript}\n\n"
        "请起草一份「结论方案草案」，要求：\n"
        "- 结构清晰，包含：核心结论、具体方案、当前仍存在的分歧点；\n"
        "- 关键结论尽量标注依据来源；\n"
        "- 语言简洁、可执行；\n"
        "- 草案要足够具体，便于其他成员逐条表决。"
    ).format(context=context, transcript=transcript)
    try:
        draft = _call_with_retry(drafter, DRAFTER_SYSTEM, draft_message, DRAFT_MAX_TOKENS, usage=usage)
    except Exception as exc:
        errors.append("起草人（{label}）起草失败：{exc}".format(label=drafter.label, exc=exc))
        draft = "（起草失败：{exc}）".format(exc=exc)

    # 3. 各方表决（盖章）
    emit(type="status", msg="各成员正在表决（盖章）…")
    vote_message = (
        "{context}\n\n"
        "以下是方案草案：\n\n{draft}\n\n"
        "（供参考）完整讨论记录：\n\n{transcript}\n\n"
        "请按模板对该草案进行表决。"
    ).format(context=context, draft=draft, transcript=transcript)

    def _vote_worker(cfg: ModelConfig) -> Vote:
        try:
            content = _call_with_retry(cfg, VOTER_SYSTEM, vote_message, VOTE_MAX_TOKENS, usage=usage)
        except Exception as exc:
            errors.append("{label}（表决）调用失败：{exc}".format(label=cfg.label, exc=exc))
            content = "（表决调用失败：{exc}）".format(exc=exc)
        return Vote(cfg.label, content)

    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        votes = list(executor.map(_vote_worker, participants))

    vote_tally = _tally_votes(votes)

    # 4. 独立评委最终裁决
    emit(type="status", msg="独立评委（{label}）正在做最终裁决…".format(label=judge.label))
    votes_text = format_votes(votes)
    tally_text = "同意 {a} 票，部分同意 {b} 票，不同意 {c} 票".format(
        a=vote_tally["同意"], b=vote_tally["部分同意"], c=vote_tally["不同意"])
    judge_message = (
        "{context}\n\n"
        "方案草案：\n\n{draft}\n\n"
        "各方表决结果：\n\n{votes_text}\n\n"
        "表决统计：{tally_text}。\n\n"
        "请作为独立评委做出最终裁决：按「少数服从多数」原则采纳多数意见，同时如实保留少数意见；"
        "输出四部分：【最终方案】【各方盖章】【保留分歧】【依据说明】。"
    ).format(context=context, draft=draft, votes_text=votes_text, tally_text=tally_text)
    try:
        final = _call_with_retry(judge, JUDGE_SYSTEM, judge_message, FINAL_MAX_TOKENS, usage=usage)
    except Exception as exc:
        errors.append("评委（{label}）裁决失败：{exc}".format(label=judge.label, exc=exc))
        final = "（裁决失败：{exc}）".format(exc=exc)

    emit(type="status", msg="完成")

    duration_seconds = int(time.time() - start_time)
    return {
        "topic": topic,
        "athletes": [p.label for p in participants],
        "per_round": per_round,
        "rounds_run": rounds_run,
        "converged": converged,
        "duration_seconds": duration_seconds,
        "usage": usage,
        "transcript": transcript,
        "draft": draft,
        "votes": votes,
        "vote_tally": vote_tally,
        "final": final,
        "judge": judge.label,
        "errors": errors,
    }
