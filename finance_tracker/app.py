import streamlit as st
import pandas as pd
import datetime
import calendar
import html
import sys
from pathlib import Path
import plotly.graph_objects as go
import plotly.express as px
from streamlit_option_menu import option_menu

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from config import save_env_values
from email_service import generate_report_content, get_mail_config_status
from ledger import (
    CATEGORIES,
    MONTHLY_BUDGET,
    add_email_job,
    delete_job,
    get_pending_jobs,
    init_db,
    load_email_jobs,
    load_transactions,
    update_transactions_from_editor,
)
from transaction_service import create_transactions_from_text

# ================= 1. 核心配置 =================
st.set_page_config(page_title="智账 Pro", layout="centered", page_icon="💸")

# --- CSS 美化 ---
st.markdown("""
<style>
    .stApp {background-color: #F7F9FB;}
    #MainMenu {visibility: hidden;} footer {visibility: hidden;}
    .css-card {background-color: #FFFFFF; border-radius: 15px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); margin-bottom: 15px;}
    .text-exp {color: #EE6C4D; font-weight: bold;}
    .text-inc {color: #4CB963; font-weight: bold;}
    .text-bal {color: #3D5A80; font-weight: bold;}
    .big-input textarea {border-radius: 20px!important; border: 2px solid #E0E1DD!important; padding: 15px;}
    .mobile-row {display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid #f0f0f0;}
    .mobile-icon {font-size: 24px; margin-right: 15px; background: #f0f2f6; border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center;}
    .report-container {font-family: "Microsoft YaHei", sans-serif; line-height: 1.75; color: #1f2937;}
    .report-container h1 {font-size: 28px; color: #111827; margin: 0 0 14px;}
    .report-container h2 {font-size: 21px; color: #1f2937; margin-top: 28px; padding-bottom: 6px; border-bottom: 1px solid #e5e7eb;}
    .report-container h3 {font-size: 17px; color: #374151; margin-top: 20px;}
    .report-container table {width: 100%; border-collapse: collapse; margin: 12px 0 18px; font-size: 14px; display: block; overflow-x: auto; white-space: nowrap;}
    .report-container th, .report-container td {border: 1px solid #e5e7eb; padding: 9px 10px;}
    .report-container th {background: #f8fafc; color: #374151;}
    .report-container blockquote {background: #eff6ff; border-left: 4px solid #2563eb; padding: 10px 12px; margin: 12px 0 18px; color: #374151;}
    .report-container code {background: #f3f4f6; border-radius: 4px; padding: 2px 4px;}
    @media (max-width: 640px) {
        .report-container h1 {font-size: 23px;}
        .report-container h2 {font-size: 18px;}
        .report-container table {font-size: 13px;}
        .report-container th, .report-container td {padding: 8px 7px;}
    }
</style>
""", unsafe_allow_html=True)


# ================= 2. 本地账本操作 =================
def load_data():
    return load_transactions()


# ================= 3. UI 组件 =================
def render_timeline_view(df):
    if df.empty:
        st.info("暂无数据")
        return
    dates = df['date'].dt.date.unique()
    for d in dates:
        day_df = df[df['date'].dt.date == d]
        st.markdown(f"**📅 {d.strftime('%m-%d %A')}**")
        for _, row in day_df.iterrows():
            icon = "💰"
            color_class = "text-exp" if row['type'] == '支出' else "text-inc"
            sym = "-" if row['type'] == '支出' else "+"
            safe_description = html.escape(str(row['description'] or row['category']))
            safe_category = html.escape(str(row['category']))

            tags_html = ""
            if row.get('tags') and str(row['tags']).strip():
                safe_tags = html.escape(str(row['tags']))
                tags_html = f"<span style='font-size:11px; background:#f0f0f0; color:#666; padding:2px 6px; border-radius:4px; margin-left:8px;'>{safe_tags}</span>"

            st.markdown(f"""
            <div class="mobile-row">
                <div style="display:flex; align-items:center;">
                    <div class="mobile-icon">{icon}</div>
                    <div>
                        <div class="mobile-desc">{safe_description} {tags_html}</div>
                        <div class="mobile-meta">{safe_category}</div>
                    </div>
                </div>
                <div class="{color_class}" style="font-size:18px; font-weight:bold;">{sym}{row['amount']:.0f}</div>
            </div>
            """, unsafe_allow_html=True)
        st.markdown("<hr style='margin: 5px 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)


def render_burndown_chart(df, budget, year, month):
    _, last_day = calendar.monthrange(year, month)
    days = list(range(1, last_day + 1))
    daily_budget = budget / last_day
    ideal = [budget - (daily_budget * d) for d in days]

    curr_df = df[(df['date'].dt.year == year) & (df['date'].dt.month == month) & (df['type'] == '支出')]
    daily_sum = curr_df.groupby(curr_df['date'].dt.day)['amount'].sum()

    actual = []
    rem = budget

    today = datetime.date.today()
    is_current_month = (today.year == year and today.month == month)
    limit_day = today.day if is_current_month else last_day

    for d in days:
        if year < today.year or (year == today.year and month < today.month) or (d <= limit_day):
            rem -= daily_sum.get(d, 0)
            actual.append(rem)
        else:
            actual.append(None)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=days, y=ideal, name="理想剩余", line=dict(color='#ddd', dash='dash')))
    fig.add_trace(go.Scatter(x=days, y=actual, name="实际剩余", line=dict(color='#EE6C4D', width=3)))
    fig.update_layout(title=f"📉 {month}月预算燃尽图", height=300, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, width="stretch")


# ================= 5. 主程序 =================
def main():
    init_db()

    selected = option_menu(
        menu_title=None,
        options=["记账", "看板", "预算", "助理", "设置"],
        icons=["chat-dots", "bar-chart-line", "wallet2", "file-earmark-text", "gear"],
        default_index=0,
        orientation="horizontal",
        styles={"nav-link": {"font-size": "14px", "margin": "0px"}}
    )

    df = load_data()

    # --- 1. 记账 ---
    if selected == "记账":
        st.markdown("<h2 style='text-align:center;'>今天记账了？</h2>", unsafe_allow_html=True)
        st.caption("💡 本地识别，不调用外部 AI。多条记录请换行输入，例如：买咖啡20")

        with st.container():
            st.markdown('<div class="big-input">', unsafe_allow_html=True)
            user_input = st.text_area("收支情况", placeholder="输入收支情况...", height=100, label_visibility="collapsed")
            st.markdown('</div>', unsafe_allow_html=True)

            if st.button("发送 🚀", type="primary", width="stretch") and user_input:
                with st.spinner("正在解析并写入本地账本..."):
                    items = create_transactions_from_text(user_input, source="streamlit")
                    if items:
                        for item in items:
                            is_income = item.get('type') == '收入'
                            color = "green" if is_income else "red"
                            symbol = "+" if is_income else "-"

                            with st.chat_message("assistant", avatar="🧾"):
                                st.markdown(f"""
                                <div style="font-size: 20px; font-weight: bold; color: {color};">
                                    {symbol} {item.get('amount', 0)} <span style="font-size:14px;color:#666">({item.get('category')})</span>
                                </div>
                                <div style="color:#888;font-size:14px">📝 {item.get('description')}</div>
                                """, unsafe_allow_html=True)
                                if item.get("local_comment"): st.info(f"💡 {item['local_comment']}")
                        st.session_state['refresh'] = True
                    else:
                        st.error("没听懂...请明确金额和事项")

    # --- 2. 看板 ---
    elif selected == "看板":
        st.header("📊 财务总览")

        if not df.empty:
            current_year = datetime.date.today().year
            current_month = datetime.date.today().month

            unique_years = sorted(df['date'].dt.year.unique().tolist(), reverse=True)
            if not unique_years: unique_years = [current_year]

            col_filter1, col_filter2 = st.columns([1, 1])
            with col_filter1:
                sel_year = st.selectbox("选择年份", unique_years, index=0, key='dash_year')
            with col_filter2:
                sel_month = st.selectbox("选择月份", range(1, 13), index=current_month - 1, key='dash_month')

            this_month_mask = (df['date'].dt.year == sel_year) & (df['date'].dt.month == sel_month)
            this_month = df[this_month_mask]

            inc = this_month[this_month['type'] == '收入']['amount'].sum()
            exp = this_month[this_month['type'] == '支出']['amount'].sum()
            bal = inc - exp

            c1, c2, c3 = st.columns(3)
            c1.markdown(
                f"""<div class="css-card"><div style="color:#888;font-size:12px">{sel_month}月收入</div><div class="text-inc" style="font-size:20px">+{inc:,.0f}</div></div>""",
                unsafe_allow_html=True)
            c2.markdown(
                f"""<div class="css-card"><div style="color:#888;font-size:12px">{sel_month}月支出</div><div class="text-exp" style="font-size:20px">-{exp:,.0f}</div></div>""",
                unsafe_allow_html=True)
            bal_col = "text-bal" if bal >= 0 else "text-exp"
            c3.markdown(
                f"""<div class="css-card"><div style="color:#888;font-size:12px">{sel_month}月结余</div><div class="{bal_col}" style="font-size:20px">{bal:+,.0f}</div></div>""",
                unsafe_allow_html=True)

            # 🔥🔥🔥 新增：收支趋势折线图 (带年份、月份筛选器) 🔥🔥🔥
            st.subheader("📈 收支趋势分析")

            col_t1, col_t2 = st.columns(2)
            with col_t1:
                # 默认选中顶部相同的年份
                try:
                    default_y_idx = unique_years.index(sel_year)
                except ValueError:
                    default_y_idx = 0
                trend_year = st.selectbox("趋势年份", unique_years, index=default_y_idx, key="trend_y")
            with col_t2:
                # 默认设为“全年”，只看年度月度情况。如果选了月份则看当月每日的情况
                trend_month_opts = ["全年"] + list(range(1, 13))
                trend_month = st.selectbox("趋势月份", trend_month_opts, index=0, key="trend_m")

            trend_df = pd.DataFrame()
            if trend_month == "全年":
                # 筛选某年，展示当年的月度情况
                trend_df = df[df['date'].dt.year == trend_year].copy()
                if not trend_df.empty:
                    trend_df['sort_key'] = trend_df['date'].dt.month
                    trend_df['period'] = trend_df['sort_key'].astype(str) + "月"
            else:
                # 筛选某年某月，展示当月每日的情况
                trend_df = df[(df['date'].dt.year == trend_year) & (df['date'].dt.month == trend_month)].copy()
                if not trend_df.empty:
                    trend_df['sort_key'] = trend_df['date'].dt.day
                    trend_df['period'] = trend_df['sort_key'].astype(str) + "日"

            if not trend_df.empty:
                # 分组聚合
                trend_grouped = trend_df.groupby(['sort_key', 'period', 'type'])['amount'].sum().reset_index()
                trend_grouped = trend_grouped.sort_values('sort_key')

                # 绘制折线图
                fig_trend = px.line(
                    trend_grouped,
                    x='period',
                    y='amount',
                    color='type',
                    markers=True,
                    text='amount',
                    color_discrete_map={'收入': '#4CB963', '支出': '#EE6C4D'}
                )
                fig_trend.update_traces(textposition="top center", texttemplate='%{text:.0f}')
                fig_trend.update_layout(
                    xaxis_title=None,
                    yaxis_title=None,
                    margin=dict(l=0, r=0, t=20, b=0),
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)'
                )
                st.plotly_chart(fig_trend, width="stretch")
            else:
                st.info("所选时间范围内暂无记录")

            st.subheader(f"📊 {sel_month}月每日流水")
            if not this_month[this_month['type'] == '支出'].empty:
                daily = this_month[this_month['type'] == '支出'].groupby(this_month['date'].dt.day)[
                    'amount'].sum().reset_index()
                daily.columns = ['日', '金额']
                fig = px.bar(daily, x='日', y='金额', text='金额', color_discrete_sequence=['#EE6C4D'])
                fig.update_traces(texttemplate='%{text:.0f}', textposition='outside')
                fig.update_layout(xaxis_title=None, yaxis_title=None, showlegend=False, height=200,
                                  margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)',
                                  plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig, width="stretch")
            else:
                st.caption("该月无支出")

            st.subheader("📝 近期明细")
            render_timeline_view(this_month.sort_values(by='date', ascending=False).head(20))
        else:
            st.info("暂无数据")

    # --- 3. 预算 ---
    elif selected == "预算":
        st.header("💰 资产管理")
        with st.container():
            budget = st.number_input("📅 月度预算基准", value=MONTHLY_BUDGET, step=100)

        if not df.empty:
            today = datetime.date.today()
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                unique_years = sorted(df['date'].dt.year.unique().tolist(), reverse=True)
                if not unique_years: unique_years = [today.year]
                b_year = st.selectbox("年份", unique_years, key='budget_year')
            with col_b2:
                b_month = st.selectbox("月份", range(1, 13), index=today.month - 1, key='budget_month')

            render_burndown_chart(df, budget, b_year, b_month)

            mask_budget_view = (df['date'].dt.year == b_year) & (df['date'].dt.month == b_month) & (
                    df['type'] == '支出')
            exp_df = df[mask_budget_view]

            if not exp_df.empty:
                st.subheader(f"🛒 {b_month}月消费构成")
                cat_group = exp_df.groupby('category')['amount'].sum().sort_values(ascending=False)

                c_chart, c_list = st.columns([1.5, 1])
                with c_chart:
                    fig_pie = px.pie(names=cat_group.index, values=cat_group.values, hole=0.5,
                                     color_discrete_sequence=px.colors.qualitative.Pastel)
                    fig_pie.update_traces(textposition='inside', textinfo='percent+label')
                    fig_pie.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=250, showlegend=False)
                    st.plotly_chart(fig_pie, width="stretch")

                with c_list:
                    st.markdown("##### 🏆 TOP 3")
                    for cat, amt in cat_group.head(3).items():
                        pct = (amt / cat_group.sum()) * 100
                        bar_c = "#EE6C4D" if pct > 30 else "#3D5A80"
                        st.markdown(f"""
                        <div style="margin-bottom:8px">
                            <div style="display:flex;justify-content:space-between;font-size:13px"><b>{cat}</b><span>¥{amt:.0f}</span></div>
                            <div style="background:#eee;height:5px;border-radius:3px"><div style="width:{pct}%;background:{bar_c};height:100%"></div></div>
                        </div>""", unsafe_allow_html=True)

                st.divider()
                cols = st.columns(4)
                for i, (cat, amt) in enumerate(cat_group.items()):
                    with cols[i % 4]:
                        st.markdown(
                            f"""<div class="css-card" style="padding:10px;text-align:center"><div style="color:#666;font-size:12px">{cat}</div><div style="font-weight:bold">¥{amt:.0f}</div></div>""",
                            unsafe_allow_html=True)
            else:
                st.info(f"{b_year}年{b_month}月 没有支出记录")

    # --- 4. 助理 ---
    elif selected == "助理":
        st.header("📄 本地财务报告")
        st.caption("基于本地账本生成统计报告，不调用外部 AI；邮件日报也使用同一份模板。")

        st.markdown('<div class="css-card">', unsafe_allow_html=True)
        col_date, col_btn = st.columns([2, 1])
        with col_date:
            analyze_date = st.date_input("选择分析基准日期", datetime.date.today())
        with col_btn:
            st.write("")
            st.write("")
            gen_btn = st.button("✨ 生成报告", type="primary", width="stretch")
        st.markdown('</div>', unsafe_allow_html=True)

        if gen_btn and not df.empty:
            with st.spinner("正在生成本地统计报告..."):
                report_content = generate_report_content(df.copy(), analyze_date)
                st.markdown('<div class="css-card report-container">', unsafe_allow_html=True)
                st.markdown(report_content)
                st.markdown('</div>', unsafe_allow_html=True)

    # --- 5. 设置 ---
    elif selected == "设置":
        st.header("⚙️ 系统设置")

        st.markdown('<div class="css-card">', unsafe_allow_html=True)
        st.subheader("📧 邮件订阅中心")
        mail_status = get_mail_config_status()
        if mail_status["ready"]:
            st.success(f"邮箱已配置：{mail_status['user']} → {', '.join(mail_status['receivers'])}")
        else:
            st.warning("邮箱未完整配置：" + "、".join(mail_status["missing"]))
        st.code(".venv\\Scripts\\python.exe finance_tracker\\scheduler.py", language="powershell")

        with st.expander("邮箱配置", expanded=not mail_status["ready"]):
            with st.form("mail_config_form"):
                mail_host = st.text_input("SMTP 服务器", value=mail_status["host"] or "smtp.qq.com")
                mail_user = st.text_input("发件邮箱", value=mail_status["user"] or "")
                mail_pass = st.text_input(
                    "邮箱授权码",
                    value="",
                    type="password",
                    placeholder="留空表示沿用已保存的授权码" if mail_status["has_password"] else "请输入邮箱授权码",
                )
                receivers = st.text_input("收件人", value=", ".join(mail_status["receivers"]))
                save_mail = st.form_submit_button("保存邮箱配置", type="primary")

            if save_mail:
                if not mail_user.strip():
                    st.error("请填写发件邮箱")
                elif not mail_pass.strip() and not mail_status["has_password"]:
                    st.error("请填写邮箱授权码")
                elif not receivers.strip():
                    st.error("请填写至少一个收件人")
                else:
                    values = {
                        "FINANCE_MAIL_HOST": mail_host.strip() or "smtp.qq.com",
                        "FINANCE_MAIL_USER": mail_user.strip(),
                        "FINANCE_MAIL_RECEIVERS": receivers.strip(),
                    }
                    if mail_pass.strip():
                        values["FINANCE_MAIL_PASS"] = mail_pass.strip()
                    save_env_values(values)
                    st.success("邮箱配置已保存")
                    st.rerun()

        c1, c2 = st.columns(2)
        with c1:
            report_date = st.date_input("生成哪天的报告？", datetime.date.today())
        with c2:
            sch_date = st.date_input("发送日期", datetime.date.today() + datetime.timedelta(days=1))
            sch_time = st.time_input("发送时间", datetime.time(8, 0))

        if st.button("📅 加入发送队列", type="primary"):
            full_time = datetime.datetime.combine(sch_date, sch_time)
            if full_time < datetime.datetime.now():
                st.warning("时间不能早于现在")
            else:
                add_email_job(report_date, full_time)
                st.success(f"已设定：{full_time} 发送 {report_date} 的报告")
                st.rerun()

        st.write("---")
        st.caption("📋 待发送队列")
        jobs = get_pending_jobs()
        if not jobs.empty:
            for _, row in jobs.iterrows():
                st.markdown(f"""
                <div style="padding:10px; border-bottom:1px solid #eee; display:flex; justify-content:space-between; align-items:center;">
                    <div><strong>ID: {row['id']}</strong> | 报告日期: {row['report_date']}<br><span style="color:#888; font-size:12px;">发送时间: {row['schedule_time']}</span></div>
                </div>
                """, unsafe_allow_html=True)
            col_del, _ = st.columns([1, 2])
            with col_del:
                del_id = st.number_input("输入ID取消", min_value=0)
                if st.button("删除任务"):
                    delete_job(del_id)
                    st.success("已删除")
                    st.rerun()
        else:
            st.info("暂无任务")

        st.caption("📜 最近任务记录")
        job_history = load_email_jobs(limit=10)
        if not job_history.empty:
            st.dataframe(job_history, hide_index=True, width="stretch")
        else:
            st.info("暂无任务记录")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="css-card">', unsafe_allow_html=True)
        st.subheader("💾 数据管理")
        st.caption("在此处可以直接修改过往记录或删除数据")
        show_deleted = st.toggle("显示已删除流水", value=False)
        manage_df = load_transactions(include_deleted=show_deleted)

        if not manage_df.empty:
            manage_df['tags'] = manage_df['tags'].fillna('')
            manage_df['is_need'] = manage_df['is_need'].fillna(0).astype(bool)
            manage_df['is_fixed'] = manage_df['is_fixed'].fillna(0).astype(bool)
            active_manage_df = manage_df[
                manage_df["status"].fillna("active") == "active"
            ].copy()
            active_manage_df["_delete"] = False
            original_rowids = active_manage_df["_rowid"].astype(int).tolist()

            column_config = {
                "_delete": st.column_config.CheckboxColumn("删除"),
                "_rowid": st.column_config.NumberColumn("ID", disabled=True),
                "id": None,
                "transaction_uid": None,
                "source": None,
                "source_message_id": None,
                "feishu_record_id": None,
                "updated_at": None,
                "sync_status": None,
                "sync_error": None,
                "source_user_open_id": None,
                "source_chat_id": None,
                "deleted_by_open_id": None,
                "date": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                "type": st.column_config.SelectboxColumn("类型", options=["支出", "收入"]),
                "category": st.column_config.SelectboxColumn("分类", options=CATEGORIES),
                "amount": st.column_config.NumberColumn("金额", format="¥ %.2f"),
                "is_need": st.column_config.CheckboxColumn("刚需?", help="1为刚需，0为享乐"),
                "is_fixed": st.column_config.CheckboxColumn("固定?", help="是否为固定支出"),
            }

            editable_columns = [
                "_delete", "_rowid", "id", "date", "type", "category", "amount",
                "description", "tags", "is_need", "is_fixed",
            ]
            edited_df = st.data_editor(
                active_manage_df[editable_columns],
                column_config=column_config,
                num_rows="dynamic",
                width="stretch",
                hide_index=True,
                key="data_editor"
            )

            if st.button("💾 保存数据修改", type="secondary"):
                try:
                    rows_to_save = edited_df[
                        ~edited_df["_delete"].fillna(False).astype(bool)
                    ].drop(columns=["_delete"])
                    save_result = update_transactions_from_editor(
                        rows_to_save,
                        original_rowids=original_rowids,
                    )
                    st.success(
                        "数据库已更新："
                        f"修改 {save_result['updated']} 条，"
                        f"新增 {save_result['created']} 条，"
                        f"删除 {save_result['deleted']} 条"
                    )
                    st.rerun()
                except (ValueError, KeyError) as exc:
                    st.error(str(exc))

            if show_deleted:
                deleted_df = manage_df[
                    manage_df["status"].fillna("active") == "deleted"
                ][
                    [
                        "id", "date", "type", "category", "amount",
                        "description", "deleted_at", "delete_reason",
                    ]
                ]
                if not deleted_df.empty:
                    st.caption("已删除流水仅供查看，不能恢复或编辑")
                    st.dataframe(deleted_df, hide_index=True, width="stretch")
        else:
            st.info("数据库暂无记录可管理")
        st.markdown('</div>', unsafe_allow_html=True)


if __name__ == "__main__":
    main()
