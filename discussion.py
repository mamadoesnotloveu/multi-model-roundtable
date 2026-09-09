"""多模型圆桌讨论的核心逻辑。

完整流程：
1. 多轮辩论：各模型轮流发言、互相点评反驳；
2. 起草：由起草人（默认 DeepSeek，可配置）根据讨论起草方案草案；
3. 表决（盖章）：每个模型对草案给出「同意 / 部分同意 / 不同意」的独立表态；
4. 修订定稿：起草人综合各方表态，产出最终定稿（最终方案 + 各方盖章 + 保留分歧）。

本文件不依赖 Streamlit，可以单独测试或复用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

# 每次回复的最大 token 数
MAX_TOKENS = 4096
# 讨论温度（越高越发散，越低越保守）
TEMPERATURE = 0.7

# 各平台的默认模型名（可在网页侧边栏修改）
DEFAULT_MODELS = {
    "claude": "claude-sonnet-5",
    "openai": "gpt-5.6-terra",
    "deepseek": "deepseek-v4-pro",
}

# 平台显示名
PROVIDER_LABELS = {
    "claude": "Claude",
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
}


@dataclass
class ModelConfig:
    """单个参与模型 / 起草人的配置。"""
    provider: str      # "claude" | "openai" | "deepseek"
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


# 辩手（参与者）的系统提示词
DEBATER_SYSTEM = (
    "你是一位圆桌讨论的参与者，正在与其他几位大模型一起讨论同一个话题。\n"
    "请遵守以下规则：\n"
    "1. 认真阅读已有的讨论记录；\n"
    "2. 主动指出他人观点中的漏洞、风险或考虑不周之处，不要一味附和；\n"
    "3. 给出你自己的独立判断、方案或补充；\n"
    "4. 语言简洁、直接、具体，避免空话套话。"
)

# 起草人的系统提示词
DRAFTER_SYSTEM = (
    "你是一位圆桌讨论的起草人（主持人），负责根据多方讨论起草一份结论方案草案。\n"
    "要求：\n"
    "1. 客观综合各方观点，不偏袒任何一方；\n"
    "2. 草案要具体、可执行，结构清晰；\n"
    "3. 对确实存在的分歧，如实在草案中标注出来；\n"
    "4. 本阶段只输出方案草案，不要下最终结论。"
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

# 修订定稿时的系统提示词
FINALIZER_SYSTEM = (
    "你是一位圆桌讨论的起草人（主持人），现在要根据各成员的投票表决结果，产出最终定稿。\n"
    "要求：\n"
    "1. 吸收合理、可执行的修改建议；\n"
    "2. 对未采纳的建议，说明原因或列入保留分歧；\n"
    "3. 如实记录每个成员的立场，不虚构一致；\n"
    "4. 严格按以下三部分输出，标题用【】括起来：\n"
    "【最终方案】综合各方意见后的最终方案\n"
    "【各方盖章】每个成员的立场与理由\n"
    "【保留分歧】仍无法调和的分歧及各方立场"
)


def missing_dependencies() -> list:
    """返回尚未安装的第三方依赖（openai / anthropic）。"""
    missing = []
    for mod in ("openai", "anthropic"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def call_model(cfg: ModelConfig, system: str, user_message: str) -> str:
    """调用单个模型，返回其文本回复。失败会抛出异常。"""
    if cfg.provider == "claude":
        from anthropic import Anthropic

        client = Anthropic(api_key=cfg.api_key, base_url=cfg.base_url or None)
        resp = client.messages.create(
            model=cfg.name,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        parts = []
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                parts.append(block.text)
        return "".join(parts).strip()

    # OpenAI 与 DeepSeek 都是 OpenAI 兼容接口，只是 base_url 不同
    from openai import OpenAI

    base_url = "https://api.deepseek.com" if cfg.provider == "deepseek" else None
    client = OpenAI(api_key=cfg.api_key, base_url=base_url)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]

    if cfg.provider == "openai":
        # 新版 OpenAI 推理模型（gpt-5.x / o 系列）：
        # 1) 用 max_completion_tokens 而非 max_tokens；
        # 2) 不支持自定义 temperature（只能用默认值），故这里不传 temperature。
        try:
            resp = client.chat.completions.create(
                model=cfg.name,
                max_completion_tokens=MAX_TOKENS,
                messages=messages,
            )
        except Exception as exc:
            # 个别旧模型仍只认 max_tokens，这里兜底重试一次
            if "max_completion_tokens" in str(exc) or "max_tokens" in str(exc):
                resp = client.chat.completions.create(
                    model=cfg.name,
                    max_tokens=MAX_TOKENS,
                    messages=messages,
                )
            else:
                raise
    else:
        # DeepSeek 用 max_tokens
        resp = client.chat.completions.create(
            model=cfg.name,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            messages=messages,
        )

    return (resp.choices[0].message.content or "").strip()


def format_transcript(utterances: list) -> str:
    """把辩论发言列表拼成一段可读的讨论记录文本。"""
    lines = []
    for u in utterances:
        lines.append(f"【{u.speaker_label} · 第 {u.round_no} 轮】\n{u.content}")
    return "\n\n".join(lines)


def format_votes(votes: list) -> str:
    """把表决结果拼成一段文本。"""
    lines = []
    for v in votes:
        lines.append(f"【{v.voter_label}】\n{v.content}")
    return "\n\n".join(lines)


def _call_participants_parallel(
    participants: list,
    system: str,
    user_message: str,
    round_no: int,
    errors: list,
) -> list:
    """同一轮内并发调用所有参与模型（它们彼此独立）。"""
    def worker(cfg: ModelConfig) -> Utterance:
        try:
            content = call_model(cfg, system, user_message)
        except Exception as exc:  # 单个模型失败不拖垮整轮
            errors.append(f"{cfg.label}（第 {round_no} 轮）调用失败：{exc}")
            content = f"（本轮调用失败，未产生回复：{exc}）"
        return Utterance(cfg.label, round_no, content)

    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        return list(executor.map(worker, participants))


def _call_voters_parallel(
    participants: list,
    system: str,
    user_message: str,
    errors: list,
) -> list:
    """表决阶段并发调用所有参与模型。"""
    def worker(cfg: ModelConfig) -> Vote:
        try:
            content = call_model(cfg, system, user_message)
        except Exception as exc:  # 单个模型失败不拖垮整轮
            errors.append(f"{cfg.label}（表决）调用失败：{exc}")
            content = f"（表决调用失败：{exc}）"
        return Vote(cfg.label, content)

    with ThreadPoolExecutor(max_workers=len(participants)) as executor:
        return list(executor.map(worker, participants))


def run_discussion(
    topic: str,
    participants: list,
    drafter: ModelConfig,
    num_rounds: int,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """执行完整流程：辩论 → 起草 → 表决 → 修订定稿。

    返回结构：
        topic        话题
        per_round    每轮辩论发言（list[list[Utterance]]）
        transcript   完整辩论记录文本
        draft        起草人产出的方案草案
        votes        各方表决结果（list[Vote]）
        final        最终定稿（最终方案 + 各方盖章 + 保留分歧）
        errors       过程中出现的错误信息
    """
    errors: list = []
    utterances: list = []
    per_round: list = []

    def announce(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    # 1. 多轮辩论
    for rnd in range(1, num_rounds + 1):
        if rnd == 1:
            user_message = f"话题：{topic}\n\n请给出你对这个话题的初步分析与建议方案。"
        else:
            transcript = format_transcript(utterances)
            user_message = (
                f"话题：{topic}\n\n"
                f"以下是目前的讨论记录：\n\n{transcript}\n\n"
                "请基于以上记录，先指出其中存在的问题或不足，再给出你的改进意见或补充观点。"
            )

        announce(f"第 {rnd}/{num_rounds} 轮：几位模型正在同时发言…")
        round_utterances = _call_participants_parallel(
            participants, DEBATER_SYSTEM, user_message, rnd, errors
        )
        utterances.extend(round_utterances)
        per_round.append(round_utterances)
        announce(f"第 {rnd}/{num_rounds} 轮完成")

    transcript = format_transcript(utterances)

    # 2. 起草方案草案
    announce(f"起草人（{drafter.label}）正在起草方案草案…")
    draft_message = (
        f"话题：{topic}\n\n"
        f"以下是完整的讨论记录：\n\n{transcript}\n\n"
        "请起草一份「结论方案草案」，要求：\n"
        "- 结构清晰，包含：核心结论、具体方案、当前仍存在的分歧点；\n"
        "- 语言简洁、可执行；\n"
        "- 草案要足够具体，便于其他成员逐条表决。"
    )
    try:
        draft = call_model(drafter, DRAFTER_SYSTEM, draft_message)
    except Exception as exc:  # 起草失败也要把错误带回去
        errors.append(f"起草人（{drafter.label}）起草失败：{exc}")
        draft = f"（起草失败：{exc}）"

    # 3. 各方表决（盖章）
    announce("各成员正在表决（盖章）…")
    vote_message = (
        f"话题：{topic}\n\n"
        f"以下是方案草案：\n\n{draft}\n\n"
        f"（供参考）完整讨论记录：\n\n{transcript}\n\n"
        "请按模板对该草案进行表决。"
    )
    votes = _call_voters_parallel(participants, VOTER_SYSTEM, vote_message, errors)

    # 4. 修订定稿
    announce("起草人正在综合表决结果，产出最终定稿…")
    votes_text = format_votes(votes)
    finalize_message = (
        f"话题：{topic}\n\n"
        f"方案草案：\n\n{draft}\n\n"
        f"各方表决结果：\n\n{votes_text}\n\n"
        "请产出最终定稿（【最终方案】【各方盖章】【保留分歧】三部分）。"
    )
    try:
        final = call_model(drafter, FINALIZER_SYSTEM, finalize_message)
    except Exception as exc:  # 定稿失败也要把错误带回去
        errors.append(f"起草人（{drafter.label}）定稿失败：{exc}")
        final = f"（定稿失败：{exc}）"

    announce("完成")

    return {
        "topic": topic,
        "per_round": per_round,
        "transcript": transcript,
        "draft": draft,
        "votes": votes,
        "final": final,
        "errors": errors,
    }
