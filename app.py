import uvicorn
import threading
import gradio as gr
import os
import asyncio
import subprocess
from fastapi import FastAPI, BackgroundTasks
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# === أضف هذا الكود هنا لتثبيت Playwright تلقائياً عند الإقلاع ===
def ensure_playwright_installed():
    try:
        browser_path = os.path.expanduser("~/.cache/ms-playwright")
        if not os.path.exists(browser_path) or not os.listdir(browser_path):
            print("🔄 جاري تثبيت متصفح Playwright (Chromium) تلقائياً...")
            subprocess.run(["playwright", "install", "chromium"], check=True)
            print("✅ تم تثبيت المتصفح بنجاح.")
    except Exception as e:
        print(f"⚠️ تحذير: فشل التثبيت التلقائي لمتصفح Playwright: {e}")

# تشغيل التحقق فوراً
ensure_playwright_installed()
# ==========================================================

os.environ["GRADIO_SSR_MODE"] = "false"
scheduler = BackgroundScheduler()
# ... (باقي الكود الخاص بك كما هو بدون تغيير)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # إقلاع فوري للمجدول بدون أي تأخير أو تحميل ثقيل
    register_scheduler_jobs()
    scheduler.start()
    print("[SCHEDULER] All 18 jobs scheduled successfully.")

    yield

    # إيقاف المجدول عند إغلاق السيرفر
    scheduler.shutdown()


app = FastAPI(title="Orchestrator Service", lifespan=lifespan)


def run_script(script_path: str, batch_size: int = None):
    try:
        env = os.environ.copy()
        if batch_size is not None:
            env["BATCH_SIZE"] = str(batch_size)
            env["CLEANER_BATCH_SIZE"] = str(batch_size)
        
        # فرض إخراج بايثون بدون تخزين مؤقت (Unbuffered) لضمان ظهور الlogs فوراً
        env["PYTHONUNBUFFERED"] = "1"

        script_dir = os.path.dirname(script_path) if os.path.dirname(script_path) else None
        script_name = os.path.basename(script_path)

        print(f"[START] Running: python3 {script_path} | BATCH_SIZE: {env.get('BATCH_SIZE', 'Default')}")
        
        # استخدام Popen بدلاً من subprocess.run لقراءة الـ stdout بشكل حي (Real-time)
        process = subprocess.Popen(
            ["python3", script_name],
            cwd=script_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # طباعة المخرجات سطر بسطر فور صدورها في الكونسول
        for line in process.stdout:
            print(line, end="")

        process.wait()

        if process.returncode == 0:
            print(f"[SUCCESS] {script_path}")
        else:
            print(f"[ERROR] {script_path} exited with code {process.returncode}")
            
    except Exception as e:
        print(f"[CRITICAL EXCEPTION] Unexpected failure running {script_path}: {e}")


def register_scheduler_jobs():
    # ── WATCHERS (كل 4 ساعات) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 */4 * * *"),
        args=["watchers/watcher_voe.py", 200],
        id="watcher_voe",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("5 */4 * * *"),
        args=["watchers/watcher_streamtape.py", 200],
        id="watcher_streamtape",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("10 */4 * * *"),
        args=["watchers/watcher_lulustream.py", 200],
        id="watcher_lulustream",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("15 */4 * * *"),
        args=["watchers/watcher_dood.py", 200],
        id="watcher_dood",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("20 */4 * * *"),
        args=["watchers/watcher_generic.py", 1],
        id="watcher_generic",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("25 */4 * * *"),
        args=["watchers/watcher_mixdrop.py", 200],
        id="watcher_mixdrop",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("30 */4 * * *"),
        args=["watchers/watcher_vk.py", 200],
        id="watcher_vk",
    )

    # ── REPAIRERS (كل 6 ساعات) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 */4 * * *"),
        args=["repairers/repairer_voe.py", 200],
        id="repairer_voe",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("10 */4 * * *"),
        args=["repairers/repairer_streamtape.py", 200],
        id="repairer_streamtape",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("20 */4 * * *"),
        args=["repairers/repairer_dood.py", 200],
        id="repairer_dood",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("30 */4 * * *"),
        args=["repairers/repairer_lulustream.py", 200],
        id="repairer_lulustream",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("40 */4 * * *"),
        args=["repairers/repairer_mixdrop.py", 200],
        id="repairer_mixdrop",
    )

    # ── RESCUE MISSIONS & SYNC (كل 6 ساعات) ──
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("45 */4 * * *"),
        args=["backup/mixdrop/mission_to_rescue_mixdrop.py", 200],
        id="rescue_mixdrop",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("50 */4 * * *"),
        args=["backup/streamtape/mission_to_rescue_STREAMTAPE.py", 200],
        id="rescue_streamtape",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("55 */4 * * *"),
        args=["backup/dood/mission_to_rescue_DOOD.py", 200],
        id="rescue_dood",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("0 */4 * * *"),
        args=["backup/lulustream/rescue_lulu_mission.py", 200],
        id="rescue_lulu",
    )
    scheduler.add_job(
        run_script,
        CronTrigger.from_crontab("15 */4 * * *"),
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


@app.get("/health")
def health_check():
    return {"status": "running", "active_jobs": len(scheduler.get_jobs())}


TASK_MAP = {
    "watcher_voe": ("watchers/watcher_voe.py", 200),
    "watcher_streamtape": ("watchers/watcher_streamtape.py", 200),
    "watcher_lulustream": ("watchers/watcher_lulustream.py", 200),
    "watcher_dood": ("watchers/watcher_dood.py", 200),
    "watcher_generic": ("watchers/watcher_generic.py", 1),
    "watcher_mixdrop": ("watchers/watcher_mixdrop.py", 200),
    "watcher_vk": ("watchers/watcher_vk.py", 200),
    
    "repairer_voe": ("repairers/repairer_voe.py", 200),
    "repairer_streamtape": ("repairers/repairer_streamtape.py", 200),
    "repairer_dood": ("repairers/repairer_dood.py", 200),
    "repairer_lulustream": ("repairers/repairer_lulustream.py", 200),
    "repairer_mixdrop": ("repairers/repairer_mixdrop.py", 200),
    
    "rescue_mixdrop": ("backup/mixdrop/mission_to_rescue_mixdrop.py", 200),
    "rescue_streamtape": ("backup/streamtape/mission_to_rescue_STREAMTAPE.py", 200),
    "rescue_dood": ("backup/dood/mission_to_rescue_DOOD.py", 200),
    "rescue_lulu": ("backup/lulustream/rescue_lulu_mission.py", 200),
    "sync_telegram": ("egy_sync_to_telegram/app.py", None),
    
    "cleaner_archive": ("repairers/cleaner_archive.py", 1),
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


def manual_trigger(mode, batch_size):
    if mode not in TASK_MAP:
        return "خطأ: النمط غير موجود"
    script_path, default_batch = TASK_MAP[mode]
    final_batch = batch_size if batch_size else default_batch

    threading.Thread(target=run_script, args=(script_path, final_batch)).start()
    return f"تم إرسال {mode} للعمل في الخلفية بنجاح!"


with gr.Blocks(title="Control Panel") as demo:
    gr.Markdown("## 🛠️ Orchestrator Control Panel")

    mode_input = gr.Dropdown(choices=list(TASK_MAP.keys()), label="اختر السكريبت")
    batch_input = gr.Number(label="BATCH_SIZE (اختياري)", value=20)
    run_btn = gr.Button("تشغيل الآن")
    output_text = gr.Textbox(label="النتيجة")

    run_btn.click(
        fn=manual_trigger, inputs=[mode_input, batch_input], outputs=output_text
    )


app = gr.mount_gradio_app(app, demo, path="/")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
