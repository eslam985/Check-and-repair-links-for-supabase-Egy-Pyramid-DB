async function scrapeAllFiles() {
    console.log("%c🚀 بدء عملية سحب البيانات الشاملة... برجاء الانتظار", "color: #00d4ff; font-weight: bold;");
    
    // 1. تجميع كل روابط الـ Edit من الصفحة
    let editLinks = Array.from(document.querySelectorAll('a.dropdown-item'))
                         .filter(a => a.href.includes('op=file_edit'))
                         .map(a => a.href);

    if (editLinks.length === 0) {
        console.error("❌ لم يتم العثور على أي ملفات في هذه الصفحة!");
        return;
    }

    let finalData = [];
    let count = 0;

    // 2. دالة لفتح كل رابط وسحب الداتا منه
    for (let url of editLinks) {
        count++;
        console.log(`⏳ جاري فحص الملف (${count}/${editLinks.length})...`);
        
        try {
            let response = await fetch(url);
            let html = await response.text();
            let parser = new DOMParser();
            let doc = parser.parseFromString(html, "text/html");

            let fileInfo = {};
            
            // سحب البيانات من الـ HTML المرجوع
            doc.querySelectorAll('.row.align-items-center').forEach(row => {
                let label = row.querySelector('label')?.innerText.trim();
                let valueDiv = row.querySelector('.col-lg-9');
                if (!label || !valueDiv) return;

                let getValue = () => {
                    let input = valueDiv.querySelector('input');
                    return input ? input.value.trim() : valueDiv.innerText.trim();
                };

                if (label.includes("Source URL")) fileInfo.source_url = getValue();
                if (label.includes("Video title")) fileInfo.video_title = getValue();
                if (label.includes("File URL")) {
                    let link = valueDiv.querySelector('a')?.href || getValue();
                    fileInfo.lulu_embed = link.replace('/c/', '/e/').replace('.com/', '.com/e/').split('?')[0];
                }
            });

            if (fileInfo.source_url) {
                finalData.push(fileInfo);
            }
        } catch (err) {
            console.error(`❌ خطأ في الرابط ${url}:`, err);
        }
    }

    // 3. النتيجة النهائية
    console.log("%c✅ تمت المهمة بنجاح!", "color: #00ff00; font-weight: bold; font-size: 16px;");
    console.table(finalData);
    console.log("نسخة جاهزة للحقن (Copy/Paste):");
    console.log(JSON.stringify(finalData));
}

// تشغيل السكربت
scrapeAllFiles();