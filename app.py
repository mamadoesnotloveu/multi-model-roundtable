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
from file_reader import read_file_text
import export_report

# 项目根目录的 .env 文件（绝对路径，避免歧义）
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_env = dotenv_values(_ENV_PATH)

# 历史讨论保存目录
_DISC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discussions")

# 本地设置文件
_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# 附件内容最大字数
MAX_ATTACH_CHARS = 50000


def _get_key(env_key: str) -> str:
    return (_env.get(env_key) or os.getenv(env_key) or "").strip()


def _get_key_any(*names: str) -> str:
    for n in names:
        v = (_env.get(n) or os.getenv(n) or "").strip()
        if v:
            return v
    return ""


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
    os.makedirs(_DISC_DIR, exist_ok=True)
    base = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fn = "{}.json".format(base)
    i = 1
    while os.path.exists(os.path.join(_DISC_DIR, fn)):
        fn = "{}_{}.json".format(base, i)
        i += 1
    with open(os.path.join(_DISC_DIR, fn), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return fn


def _list_discussions() -> list:
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
    """渲染一次讨论（最终裁决 + 完整过程，辩论并排显示）。"""
    st.markdown("## 🎯 最终方案（评委裁决）")
    st.markdown(data.get("final", "") or "（无内容）")

    atts = data.get("attachments", [])
    if atts:
        names = "、".join(a.get("name", "") for a in atts if isinstance(a, dict))
        st.caption("📎 附件：{}".format(names))

    tally = data.get("vote_tally", {})
    if tally:
        st.markdown(
            "**表决统计：** 同意 {} · 部分同意 {} · 不同意 {}".format(
                tally.get("同意", 0), tally.get("部分同意", 0), tally.get("不同意", 0))
        )
    meta_extra = []
    if data.get("rounds_run"):
        meta_extra.append("实际轮数 {}".format(data["rounds_run"]))
    if data.get("converged"):
        meta_extra.append("已收敛提前结束")
    if data.get("judge"):
        meta_extra.append("评委 {}".format(data["judge"]))
    if meta_extra:
        st.caption("　|　".join(meta_extra))

    with st.expander("📜 完整过程：草案 → 表决 → 裁决", expanded=False):
        st.markdown("### ① 方案草案")
        st.markdown(data.get("draft", "") or "（无内容）")
        st.divider()
        st.markdown("### ② 各方表决（盖章）")
        for v in data.get("votes", []):
            st.markdown("**{}**".format(v.get("voter", "")))
            st.markdown(v.get("content", "") or "（无内容）")
            st.divider()
        st.markdown("### ③ 辩论过程（并排）")
        for rnd in data.get("per_round", []):
            if not rnd:
                continue
            st.markdown("#### 第 {} 轮".format(rnd[0].get("round", "?")))
            cols = st.columns(len(rnd))
            for u, col in zip(rnd, cols):
                with col:
                    st.markdown("**{}**".format(u.get("speaker", "")))
                    st.markdown(u.get("content", "") or "（无内容）")


def _export_buttons(data: dict) -> None:
    """渲染一键导出 Word / PDF 按钮（成果报告，不含话题/附件/过程）。"""
    ts = data.get("time", "").replace(":", "").replace("-", "").replace(" ", "_") or "discussion"
    try:
        word_bytes = export_report.render_word(data)
        pdf_bytes = export_report.render_pdf(data)
    except Exception as exc:
        st.error("导出失败：{}".format(exc))
        return
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇️ 导出 Word（成果报告）",
            data=word_bytes,
            file_name="圆桌讨论成果_{}.docx".format(ts),
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    with c2:
        st.download_button(
            "⬇️ 导出 PDF（成果报告）",
            data=pdf_bytes,
            file_name="圆桌讨论成果_{}.pdf".format(ts),
            mime="application/pdf",
        )


st.set_page_config(page_title="多模型圆桌讨论", layout="wide")

st.title("🗣️ 多模型圆桌讨论")
st.caption(
    "让 Claude、GPT、DeepSeek、Grok 一起讨论同一个话题，经过辩论、起草、表决、评委裁决，最终产出共识方案。"
    "全程只在你本机运行，不经过任何第三方服务器。"
)

missing = missing_dependencies()
if missing:
    st.error("缺少依赖：" + "、".join(missing))
    st.markdown("请先运行 `pip install -r requirements.txt` 后重新启动。")
    st.stop()

# API Key（从 .env / 环境变量读取）
KEYS = {
    "claude": _get_key("ANTHROPIC_API_KEY"),
    "openai": _get_key("OPENAI_API_KEY"),
    "deepseek": _get_key("DEEPSEEK_API_KEY"),
    "grok": _get_key_any("XAI_API_KEY", "GROK_API_KEY"),
}

ANTHROPIC_BASE_URL = (_env.get("ANTHROPIC_BASE_URL") or "").strip()

# 模型名种子（跨刷新保留）
_settings = _load_settings()
_saved_models = _settings.get("models", {})
if not isinstance(_saved_models, dict):
    _saved_models = {}
for pid, default_name in DEFAULT_MODELS.items():
    if "model_{}".format(pid) not in st.session_state:
        st.session_state["model_{}".format(pid)] = _saved_models.get(pid, default_name)

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
            st.success("{}：已配置 ✓".format(PROVIDER_LABELS[pid]))
        else:
            st.error("{}：未配置".format(PROVIDER_LABELS[pid]))
    st.caption(
        "Key 从项目根目录的 `.env` 文件读取（更安全）。\n\n"
        "新增 Grok：在 `.env` 里填 `XAI_API_KEY=`（xAI 官方 Key）。"
    )

    st.header("⚙️ 模型名（可修改）")
    for pid in ("claude", "openai", "deepseek", "grok"):
        st.text_input("{} 模型".format(PROVIDER_LABELS[pid]), key="model_{}".format(pid))

    st.header("🎛️ 讨论设置")
    num_rounds = st.slider(
        "讨论轮数（上限）",
        min_value=1,
        max_value=10,
        key="num_rounds",
        help="第 1 轮初步观点，之后每轮互相反驳；观点收敛时会提前结束。",
    )

    available = [pid for pid in KEYS if KEYS[pid]]
    st.write("已配置 Key：{}/4".format(len(available)))

    st.header("🎙️ 角色分配")
    judge_choice = None
    athletes = []
    drafter_choice = None
    if available:
        default_judge = "grok" if "grok" in available else available[-1]
        judge_choice = st.selectbox(
            "独立评委（不做运动员）",
            available,
            index=available.index(default_judge),
            key="judge_sel",
            format_func=lambda p: PROVIDER_LABELS[p],
        )
        athletes = [p for p in available if p != judge_choice]
        if athletes:
            if st.session_state.get("drafter_sel") not in athletes:
                st.session_state["drafter_sel"] = "deepseek" if "deepseek" in athletes else athletes[0]
            drafter_choice = st.selectbox(
                "起草人（运动员之一）",
                athletes,
                key="drafter_sel",
                format_func=lambda p: PROVIDER_LABELS[p],
            )
            st.caption("评委独立裁决；起草人负责起草草案；其余为运动员参与辩论。")
    else:
        st.info("请先在 .env 填写 API Key。")

# 写回设置
_new_settings = {
    "models": {pid: st.session_state["model_{}".format(pid)] for pid in DEFAULT_MODELS},
    "num_rounds": st.session_state["num_rounds"],
}
if _new_settings != _settings:
    _save_settings(_new_settings)


def _clear_topic() -> None:
    st.session_state["topic"] = ""


# ---------------- 主区域 ----------------
tab_new, tab_history = st.tabs(["💬 新讨论", "📂 历史讨论"])

with tab_new:
    topic = st.text_area(
        "话题",
        key="topic",
        height=120,
        placeholder="例如：如何在家高效健身？ / 我该不该辞职创业？ / 如何给一款新产品定价？",
    )

    uploaded_files = st.file_uploader(
        "📎 附件（可选）：Word / PPT / Excel，文件内容会被提取成文字供所有模型一起讨论",
        type=["docx", "pptx", "xlsx"],
        accept_multiple_files=True,
    )

    attachments = []
    attachment_text = ""
    if uploaded_files:
        total_chars = 0
        for uf in uploaded_files:
            try:
                txt = read_file_text(uf.name, uf.getvalue())
            except Exception as exc:
                st.warning("文件 {} 解析失败：{}".format(uf.name, exc))
                continue
            attachments.append({"name": uf.name, "text": txt})
            total_chars += len(txt)
        if attachments:
            st.caption("已读取 {} 个附件，共 {} 字。".format(len(attachments), total_chars))
            blocks = ["【附件：{}】\n{}".format(a["name"], a["text"]) for a in attachments]
            attachment_text = "\n\n".join(blocks)
            if len(attachment_text) > MAX_ATTACH_CHARS:
                attachment_text = attachment_text[:MAX_ATTACH_CHARS] + "\n\n（附件内容过长，已截断）"
                st.warning("附件总字数超过 {}，讨论时只使用了前 {} 字。".format(MAX_ATTACH_CHARS, MAX_ATTACH_CHARS))

    if len(athletes) < 2:
        st.warning("至少需要配置 3 个平台的 API Key（2 名运动员 + 1 名评委）才能讨论。")

    can_run = bool(topic.strip()) and len(athletes) >= 2

    col_run, col_clear = st.columns([4, 1])
    with col_run:
        run_clicked = st.button("🚀 开始讨论", type="primary", disabled=not can_run)
    with col_clear:
        st.button("🧹 清空话题", on_click=_clear_topic)

    if run_clicked:
        athlete_cfgs = [
            ModelConfig(
                provider=pid,
                name=st.session_state["model_{}".format(pid)],
                api_key=KEYS[pid],
                label=PROVIDER_LABELS[pid],
                base_url=ANTHROPIC_BASE_URL if pid == "claude" else "",
            )
            for pid in athletes
        ]
        drafter_cfg = ModelConfig(
            provider=drafter_choice,
            name=st.session_state["model_{}".format(drafter_choice)],
            api_key=KEYS[drafter_choice],
            label=PROVIDER_LABELS[drafter_choice],
            base_url=ANTHROPIC_BASE_URL if drafter_choice == "claude" else "",
        )
        judge_cfg = ModelConfig(
            provider=judge_choice,
            name=st.session_state["model_{}".format(judge_choice)],
            api_key=KEYS[judge_choice],
            label=PROVIDER_LABELS[judge_choice],
            base_url=ANTHROPIC_BASE_URL if judge_choice == "claude" else "",
        )

        # 流式并排显示
        stream_header = st.empty()
        cols = st.columns(len(athlete_cfgs))
        placeholders = {a.label: c.empty() for a, c in zip(athlete_cfgs, cols)}
        buffers = {a.label: "" for a in athlete_cfgs}
        state = {"round": 1}

        def handle_event(ev: dict) -> None:
            t = ev.get("type")
            if t == "status":
                stream_header.markdown("⏳ " + ev.get("msg", ""))
            elif t == "round_start":
                state["round"] = ev.get("round", 1)
                for a in athlete_cfgs:
                    buffers[a.label] = ""
                    placeholders[a.label].markdown(
                        "**{}**\n\n（第 {} 轮 · 思考中…）".format(a.label, state["round"]))
            elif t == "chunk":
                label = ev.get("label")
                buffers[label] = buffers.get(label, "") + ev.get("text", "")
                placeholders[label].markdown(
                    "**{}** · 第 {} 轮\n\n{}".format(label, state["round"], buffers[label]))

        result = run_discussion(
            topic, athlete_cfgs, drafter_cfg, judge_cfg, num_rounds,
            event_cb=handle_event,
            attachment_text=attachment_text,
        )
        stream_header.empty()
        for a in athlete_cfgs:
            placeholders[a.label].empty()

        data = {
            "topic": topic.strip(),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "drafter": drafter_cfg.label,
            "judge": judge_cfg.label,
            "num_rounds": num_rounds,
            "rounds_run": result["rounds_run"],
            "converged": result["converged"],
            "athletes": result["athletes"],
            "duration_seconds": result["duration_seconds"],
            "usage": result["usage"],
            "attachments": attachments,
            "per_round": [
                [{"speaker": u.speaker_label, "round": u.round_no, "content": u.content} for u in rnd]
                for rnd in result["per_round"]
            ],
            "draft": result["draft"],
            "votes": [{"voter": v.voter_label, "content": v.content} for v in result["votes"]],
            "vote_tally": result["vote_tally"],
            "final": result["final"],
            "errors": result["errors"],
        }

        try:
            _save_discussion(data)
            st.success("✅ 本次讨论已自动保存，可在「📂 历史讨论」标签页回看。")
        except OSError as exc:
            st.warning("保存失败：{}".format(exc))

        for err in data["errors"]:
            st.error(err)

        _render_discussion(data)

        st.markdown("---")
        _export_buttons(data)

with tab_history:
    items = _list_discussions()
    if not items:
        st.info("还没有保存的讨论。在「💬 新讨论」里完成一次讨论后，会自动保存到这里。")
    else:
        labels = ["{}　·　{}".format(it["time"], it["topic"][:40]) for it in items]

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
            "**话题：** {}　·　**时间：** {}　·　**起草人：** {}　·　**评委：** {}".format(
                item["topic"], item["time"],
                item["data"].get("drafter", ""), item["data"].get("judge", ""))
        )
        st.divider()
        _render_discussion(item["data"])

        st.markdown("---")
        _export_buttons(item["data"])

        st.markdown("---")
        st.markdown("### 多份讨论横向对比")
        cmp = st.multiselect(
            "选择要对比的讨论（2~4 份），会并排显示各自的最终方案",
            range(len(items)),
            format_func=lambda i: labels[i],
            key="history_cmp",
        )
        if len(cmp) >= 2:
            cmp_cols = st.columns(len(cmp))
            for i, col in zip(cmp, cmp_cols):
                it = items[i]
                with col:
                    st.markdown("**{}**".format(it["time"]))
                    st.markdown("*{}*".format(it["topic"][:40]))
                    st.markdown(it["data"].get("final", "") or "（无内容）")
