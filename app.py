"""多模型圆桌讨论 —— Streamlit 网页入口。

用法：streamlit run app.py
"""
import datetime
import json
import os

import streamlit as st
from dotenv import dotenv_values

from discussion import (
    DEFAULT_MODELS,
    PROVIDER_LABELS,
    ModelConfig,
    missing_dependencies,
    run_discussion,
)

# 项目根目录的 .env 文件（绝对路径，避免歧义）
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
# 每次运行都直接重新读取 .env 文件内容，不写 os.environ，避免残留旧值
_env = dotenv_values(_ENV_PATH)

# 历史讨论保存目录
_DISC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discussions")

# 本地设置文件（保存模型名、讨论轮数等非敏感设置，跨刷新/重启保留）
_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")


def _get_key(env_key: str) -> str:
    """优先读 .env 文件，其次读环境变量；都为空则返回空字符串。"""
    return (_env.get(env_key) or os.getenv(env_key) or "").strip()


def _load_settings() -> dict:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_settings(data: dict) -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _save_discussion(data: dict) -> str:
    """把一次讨论保存为 discussions/ 下的 JSON 文件，返回文件名。"""
    os.makedirs(_DISC_DIR, exist_ok=True)
    base = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fn = f"{base}.json"
    i = 1
    while os.path.exists(os.path.join(_DISC_DIR, fn)):
        fn = f"{base}_{i}.json"
        i += 1
    with open(os.path.join(_DISC_DIR, fn), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return fn


def _list_discussions() -> list:
    """列出所有已保存的讨论（新的在前）。"""
    if not os.path.isdir(_DISC_DIR):
        return []
    items = []
    for fn in os.listdir(_DISC_DIR):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(_DISC_DIR, fn)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        items.append({
            "file": fn,
            "time": d.get("time", ""),
            "topic": d.get("topic", "") or "(无标题)",
            "data": d,
        })
    items.sort(key=lambda x: x["file"], reverse=True)
    return items


def _render_discussion(data: dict) -> None:
    """按统一结构渲染一次讨论（最终方案 + 完整过程）。"""
    st.markdown("## 🎯 最终方案（定稿）")
    st.markdown(data.get("final", "") or "（无内容）")

    with st.expander("📜 完整过程：草案 → 表决 → 定稿", expanded=False):
        st.markdown("### ① 方案草案")
        st.markdown(data.get("draft", "") or "（无内容）")
        st.divider()
        st.markdown("### ② 各方表决（盖章）")
        for v in data.get("votes", []):
            st.markdown(f"**{v.get('voter', '')}**")
            st.markdown(v.get("content", "") or "（无内容）")
            st.divider()
        st.markdown("### ③ 辩论过程")
        for rnd in data.get("per_round", []):
            if not rnd:
                continue
            st.markdown(f"#### 第 {rnd[0].get('round', '?')} 轮")
            for u in rnd:
                st.markdown(f"**{u.get('speaker', '')}**")
                st.markdown(u.get("content", "") or "（无内容）")
                st.divider()


st.set_page_config(page_title="多模型圆桌讨论", layout="wide")

st.title("🗣️ 多模型圆桌讨论")
st.caption(
    "让 Claude、GPT、DeepSeek 一起讨论同一个话题，经过辩论、起草、表决，最终产出共识方案。"
    "全程只在你本机运行，不经过任何第三方服务器。"
)

# 依赖检查
missing = missing_dependencies()
if missing:
    st.error("缺少依赖：" + "、".join(missing))
    st.markdown(
        "请先在终端进入本项目目录，运行：\n\n"
        "`pip install -r requirements.txt`\n\n"
        "然后重新启动本页面。"
    )
    st.stop()

# API Key 一律从 .env / 环境变量读取，避免在浏览器里输入
KEYS = {
    "claude": _get_key("ANTHROPIC_API_KEY"),
    "openai": _get_key("OPENAI_API_KEY"),
    "deepseek": _get_key("DEEPSEEK_API_KEY"),
}

# Claude 的接口地址：留空 = Anthropic 官方；使用 B.AI 等中转服务时填它的地址
ANTHROPIC_BASE_URL = (_env.get("ANTHROPIC_BASE_URL") or "").strip()

# 模型名 / 讨论设置：优先读本地 settings.json（跨刷新保留），否则用默认值
_settings = _load_settings()
_saved_models = _settings.get("models", {})
if not isinstance(_saved_models, dict):
    _saved_models = {}

for pid, default_name in DEFAULT_MODELS.items():
    if f"model_{pid}" not in st.session_state:
        st.session_state[f"model_{pid}"] = _saved_models.get(pid, default_name)

try:
    _saved_rounds = int(_settings.get("num_rounds", 2))
except (TypeError, ValueError):
    _saved_rounds = 2
if "num_rounds" not in st.session_state:
    st.session_state["num_rounds"] = _saved_rounds

# ---------------- 侧边栏 ----------------
with st.sidebar:
    st.header("🔑 API Key 状态")
    for pid, key in KEYS.items():
        if key:
            st.success(f"{PROVIDER_LABELS[pid]}：已配置 ✓")
        else:
            st.error(f"{PROVIDER_LABELS[pid]}：未配置")
    st.caption(
        "Key 从项目根目录的 `.env` 文件读取（更安全，不经过浏览器/网络）。\n\n"
        "配置方法：把 `.env.example` 复制为 `.env`，填入三把 Key，再重启本页面。"
    )

    st.header("⚙️ 模型名（可修改）")
    st.text_input("Claude 模型", key="model_claude")
    st.text_input("OpenAI 模型", key="model_openai")
    st.text_input("DeepSeek 模型", key="model_deepseek")
    st.caption("模型名会随时间更新，若某模型报“找不到模型”，来此处改成最新名称。改动会自动保存。")

    st.header("🎛️ 讨论设置")
    num_rounds = st.slider(
        "讨论轮数",
        min_value=1,
        max_value=10,
        key="num_rounds",
        help="第 1 轮是初步观点，之后每轮互相点评反驳。轮数越多上下文越长、费用越高。",
    )

    available = [pid for pid, k in KEYS.items() if k]
    st.write(f"已配置 Key：{len(available)}/3")

    st.header("🎙️ 起草人 / 主持人")
    if available:
        default_drafter = "deepseek" if "deepseek" in available else available[0]
        drafter_choice = st.selectbox(
            "选择起草人（默认 DeepSeek）",
            available,
            index=available.index(default_drafter),
            format_func=lambda pid: PROVIDER_LABELS[pid],
        )
        st.caption("起草人负责：起草方案草案 + 综合表决结果产出最终定稿。")
    else:
        st.info("请先在 .env 中填写至少一个 API Key。")
        drafter_choice = None

# 把当前设置写回 settings.json（仅在变化时写，保证刷新/重启后不丢）
_new_settings = {
    "models": {
        "claude": st.session_state["model_claude"],
        "openai": st.session_state["model_openai"],
        "deepseek": st.session_state["model_deepseek"],
    },
    "num_rounds": st.session_state["num_rounds"],
}
if _new_settings != _settings:
    _save_settings(_new_settings)


def _clear_topic() -> None:
    st.session_state["topic"] = ""


# ---------------- 主区域：两个标签页 ----------------
tab_new, tab_history = st.tabs(["💬 新讨论", "📂 历史讨论"])

with tab_new:
    topic = st.text_area(
        "话题",
        key="topic",
        height=120,
        placeholder="例如：如何在家高效健身？ / 我该不该辞职创业？ / 如何给一款新产品定价？",
    )

    if len(available) < 2:
        st.warning("至少需要配置 2 个平台的 API Key（.env 文件）才能进行“讨论”。")

    can_run = bool(topic.strip()) and len(available) >= 2

    col_run, col_clear = st.columns([4, 1])
    with col_run:
        run_clicked = st.button("🚀 开始讨论", type="primary", disabled=not can_run)
    with col_clear:
        st.button("🧹 清空话题", on_click=_clear_topic)

    if run_clicked:
        participants = [
            ModelConfig(
                provider=pid,
                name=st.session_state[f"model_{pid}"],
                api_key=KEYS[pid],
                label=PROVIDER_LABELS[pid],
                base_url=ANTHROPIC_BASE_URL if pid == "claude" else "",
            )
            for pid in available
        ]
        drafter = ModelConfig(
            provider=drafter_choice,
            name=st.session_state[f"model_{drafter_choice}"],
            api_key=KEYS[drafter_choice],
            label=PROVIDER_LABELS[drafter_choice],
            base_url=ANTHROPIC_BASE_URL if drafter_choice == "claude" else "",
        )

        progress = st.empty()

        def announce(msg: str) -> None:
            progress.markdown(f"⏳ {msg}")

        with st.spinner("圆桌讨论进行中，请稍候…"):
            result = run_discussion(topic, participants, drafter, num_rounds, announce)
        progress.empty()

        # 组装成可保存、可回看的统一结构
        data = {
            "topic": topic.strip(),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "drafter": drafter.label,
            "num_rounds": num_rounds,
            "per_round": [
                [{"speaker": u.speaker_label, "round": u.round_no, "content": u.content} for u in rnd]
                for rnd in result["per_round"]
            ],
            "draft": result["draft"],
            "votes": [{"voter": v.voter_label, "content": v.content} for v in result["votes"]],
            "final": result["final"],
            "errors": result["errors"],
        }

        # 自动保存到历史记录
        try:
            _save_discussion(data)
            st.success("✅ 本次讨论已自动保存，可在「📂 历史讨论」标签页回看。")
        except OSError as exc:
            st.warning(f"保存失败：{exc}")

        for err in data["errors"]:
            st.error(err)

        _render_discussion(data)

with tab_history:
    items = _list_discussions()
    if not items:
        st.info("还没有保存的讨论。在「💬 新讨论」里完成一次讨论后，会自动保存到这里。")
    else:
        labels = [f"{it['time']}　·　{it['topic']}" for it in items]
        if st.session_state.get("history_sel", 0) >= len(items):
            st.session_state["history_sel"] = 0
        st.selectbox(
            "选择要回看的讨论",
            range(len(items)),
            format_func=lambda i: labels[i],
            key="history_sel",
        )
        sel = st.session_state.get("history_sel", 0)
        if sel >= len(items):
            sel = 0
        item = items[sel]

        st.markdown(
            f"**话题：** {item['topic']}　·　**时间：** {item['time']}　·　"
            f"**起草人：** {item['data'].get('drafter', '')}"
        )
        st.divider()
        _render_discussion(item["data"])
