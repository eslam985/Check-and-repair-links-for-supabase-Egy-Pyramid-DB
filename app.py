import gradio as gr
import os
import asyncio
import subprocess
from fastapi import FastAPI, BackgroundTasks
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

app = FastAPI(title="Orchestrator Service")
scheduler = BackgroundScheduler()


def run_script(script_path: str, batch_size: int = None):
    env = os.environ.copy()
    if batch_size is not None:
        env["BATCH_SIZE"] = str(batch_size)
        env["CLEANER_BATCH_SIZE"] = str(batch_size)

    script_dir = os.path.dirname(script_path) if os.path.dirname(script_path) else None
    script_name = os.path.basename(script_path)

    print(
        f"[START] Running: python3 {script_path} | BATCH_SIZE: {env.get('BATCH_SIZE', 'Default')}"
    )
    result = subprocess.run(
        ["python3", script_name], cwd=script_dir, env=env, capture_output=True, text=True
    )

    if result.returncode == 0:
        print(f"[SUCCESS] {script_path}\n{result.stdout}")
    else:
        print(f"[ERROR] {script_path}\n{result.stderr}")


@app.on_event("startup")
def start_scheduler():
    # ── WATCHERS (كل 6 ساعات) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 */6 * * *"),
        args=["watchers/watcher_voe.py", 100],
        id="watcher_voe",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("5 */6 * * *"),
        args=["watchers/watcher_streamtape.py", 100],
        id="watcher_streamtape",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("10 */6 * * *"),
        args=["watchers/watcher_lulustream.py", 100],
        id="watcher_lulustream",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("15 */6 * * *"),
        args=["watchers/watcher_dood.py", 100],
        id="watcher_dood",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("20 */6 * * *"),
        args=["watchers/watcher_generic.py", 1],
        id="watcher_generic",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("25 */6 * * *"),
        args=["watchers/watcher_mixdrop.py", 100],
        id="watcher_mixdrop",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("30 */6 * * *"),
        args=["watchers/watcher_vk.py", 100],
        id="watcher_vk",
    )

    # ── REPAIRERS (كل 6 ساعات) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 */6 * * *"),
        args=["repairers/repairer_voe.py", 100],
        id="repairer_voe",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("10 */6 * * *"),
        args=["repairers/repairer_streamtape.py", 100],
        id="repairer_streamtape",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("20 */6 * * *"),
        args=["repairers/repairer_dood.py", 100],
        id="repairer_dood",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("30 */6 * * *"),
        args=["repairers/repairer_lulustream.py", 100],
        id="repairer_lulustream",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("40 */6 * * *"),
        args=["repairers/repairer_mixdrop.py", 100],
        id="repairer_mixdrop",
    )

    # ── RESCUE MISSIONS & SYNC (كل 6 ساعات) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("45 */6 * * *"),
        args=["backup/mixdrop/mission_to_rescue_mixdrop.py", 10],
        id="rescue_mixdrop",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("50 */6 * * *"),
        args=["backup/streamtape/mission_to_rescue_STREAMTAPE.py", 10],
        id="rescue_streamtape",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("55 */6 * * *"),
        args=["backup/dood/mission_to_rescue_DOOD.py", 10],
        id="rescue_dood",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 */6 * * *"),
        args=["backup/lulustream/rescue_lulu_mission.py", 10],
        id="rescue_lulu",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("15 */6 * * *"),
        args=["egy_sync_to_telegram/app.py"],
        id="sync_telegram",
    )

    # ── CLEANERS (يومياً الساعة 3 فجراً) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 3 * * *"),
        args=["repairers/cleaner_vk.py", 100],
        id="cleaner_vk",
    )

    scheduler.start()
    print("[SCHEDULER] All 18 jobs scheduled successfully.")


@app.get("/")
def health_check():
    return {"status": "running", "active_jobs": len(scheduler.get_jobs())}


TASK_MAP = {
    "watcher_voe": ("watchers/watcher_voe.py", 100),
    "watcher_streamtape": ("watchers/watcher_streamtape.py", 100),
    "watcher_lulustream": ("watchers/watcher_lulustream.py", 100),
    "watcher_dood": ("watchers/watcher_dood.py", 100),
    "watcher_generic": ("watchers/watcher_generic.py", 1),
    "watcher_mixdrop": ("watchers/watcher_mixdrop.py", 100),
    "watcher_vk": ("watchers/watcher_vk.py", 100),
    "repairer_voe": ("repairers/repairer_voe.py", 100),
    "repairer_streamtape": ("repairers/repairer_streamtape.py", 100),
    "repairer_dood": ("repairers/repairer_dood.py", 100),
    "repairer_lulustream": ("repairers/repairer_lulustream.py", 100),
    "repairer_mixdrop": ("repairers/repairer_mixdrop.py", 100),
    "rescue_mixdrop": ("backup/mixdrop/mission_to_rescue_mixdrop.py", 10),
    "rescue_streamtape": ("backup/streamtape/mission_to_rescue_STREAMTAPE.py", 10),
    "rescue_dood": ("backup/dood/mission_to_rescue_DOOD.py", 10),
    "rescue_lulu": ("backup/lulustream/rescue_lulu_mission.py", 10),
    "sync_telegram": ("egy_sync_to_telegram/app.py", None),
    "cleaner_archive": ("repairers/cleaner_archive.py", 5),
    "cleaner_vk": ("repairers/cleaner_vk.py", 100),
}


@app.post("/run-task")
def trigger_task(mode: str, background_tasks: BackgroundTasks, batch_size: int = None):
    if mode not in TASK_MAP:
        return {"error": f"Invalid mode. Available modes: {list(TASK_MAP.keys())}"}

    script_path, default_batch = TASK_MAP[mode]
    final_batch = batch_size if batch_size is not None else default_batch

    background_tasks.add_task(run_script, script_path, final_batch)
    return {
        "message": f"Mode '{mode}' ({script_path}) queued successfully with BATCH_SIZE={final_batch}."
    }




# تثبيت متصفح Playwright تلقائياً عند أول تشغيل للسيرفر
subprocess.run(["playwright", "install", "chromium"])

# واجهة التشغيل اليدوي
def manual_trigger(mode, batch_size):
    if mode not in TASK_MAP:
        return f"خطأ: النمط غير موجود"
    script_path, default_batch = TASK_MAP[mode]
    final_batch = batch_size if batch_size else default_batch
    
    # تشغيل المهمة
    run_script(script_path, final_batch)
    return f"تم تشغيل {mode} بنجاح!"

# بناء الواجهة الرسومية (بديل workflow_dispatch)
with gr.Blocks(title="Control Panel") as demo:
    gr.Markdown("## 🛠️ Orchestrator Control Panel")
    
    mode_input = gr.Dropdown(choices=list(TASK_MAP.keys()), label="اختر السكريبت")
    batch_input = gr.Number(label="BATCH_SIZE (اختياري)", value=100)
    run_btn = gr.Button("تشغيل الآن")
    output_text = gr.Textbox(label="النتيجة")
    
    run_btn.click(fn=manual_trigger, inputs=[mode_input, batch_input], outputs=output_text)

# تشغيل الواجهة والسيرفر
if __name__ == "__main__":
    start_scheduler()
    demo.launch()