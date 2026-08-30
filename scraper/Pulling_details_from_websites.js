// ##################################################
//  اسكربت stardusttv لاستخراج الصوره والاسم والعنوان من المتصح
// ##################################################
(function () {
    // 1. محاولة ذكية لسحب العنوان (يدور في h1 أو أول عنصر خطه كبير)
    const title = document.querySelector('h1') ? document.querySelector('h1').innerText :
        Array.from(document.querySelectorAll('div, span, p')).find(el => window.getComputedStyle(el).fontSize.replace('px', '') > 20)?.innerText || "لم يتم العثور على عنوان";

    // 2. محاولة سحب القصة (يدور في العناصر اللي فيها نص طويل)
    const story = Array.from(document.querySelectorAll('div, p, span'))
        .find(el => el.innerText.length > 100 && !el.querySelector('div'))?.innerText || "لم يتم العثور على وصف";

    // 3. سحب البوستر
    let poster = "لم يتم العثور على بوستر";
    const posterImg = document.querySelector('img[alt="poster"]');
    if (posterImg) {
        poster = posterImg.srcset ? posterImg.srcset.split(',')[0].split(' ')[0] : posterImg.src;
    }

    // --- إنشاء زر النسخ في الصفحة ---
    const copyBtn = document.createElement('button');
    copyBtn.innerText = '📋 نسخ رابط الصورة';
    copyBtn.style = `
        position: fixed; top: 20px; left: 20px; z-index: 9999;
        padding: 15px 25px; background: #ff4757; color: white;
        border: none; border-radius: 8px; cursor: pointer;
        font-family: sans-serif; font-weight: bold; box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    `;

    copyBtn.onclick = function () {
        navigator.clipboard.writeText(poster).then(() => {
            copyBtn.innerText = '✅ تم النسخ!';
            copyBtn.style.background = '#2ed573';
            setTimeout(() => copyBtn.remove(), 2000);
        });
    };

    document.body.appendChild(copyBtn);

    // عرض النتيجة في الكونسول برضه للتحقق
    const result = { "العنوان": title.trim(), "القصة": story.trim(), "الرابط": poster };
    console.table(result);
})();


// ##################################################
//  اسكربت ريال شوت لاستخراج الصوره والاسم والعنوان من المتصح
// ##################################################

(function () {
    // 1. سحب العنوان (الأولوية لـ h1 اللي فيه اسم المسلسل)
    const title = document.querySelector('h1')?.innerText?.trim() ||
        document.querySelector('h3')?.innerText?.replace('حبكة', '').trim() ||
        "لم يتم العثور على عنوان";

    // 2. سحب القصة (استهداف كلاس rich-text الخاص بـ ReelShort)
    const story = document.querySelector('.rich-text')?.innerText?.trim() ||
        Array.from(document.querySelectorAll('div, p'))
            .find(el => el.innerText.length > 50 && el.innerText.includes(' '))?.innerText?.trim() ||
        "لم يتم العثور على وصف";

    // 3. سحب رابط الصورة الأصلي (تنظيف روابط Next.js)
    let poster = "لم يتم العثور على بوستر";
    // بندور على الصورة اللي داخل div واخد دور "cover" أو alt فيه اسم المسلسل
    const imgEl = document.querySelector('div[role="cover"] img[srcset], img[alt*="' + title.slice(0, 5) + '"]');

    if (imgEl) {
        let rawSrc = imgEl.srcset ? imgEl.srcset.split(',').pop().trim().split(' ')[0] : imgEl.src;
        // لو الرابط فيه نظام Next.js (_next/image?url=...) بنستخلص الرابط الأصلي منه
        if (rawSrc.includes('url=')) {
            poster = decodeURIComponent(rawSrc.split('url=')[1].split('&')[0]);
        } else {
            poster = rawSrc;
        }
    }

    // --- إنشاء زر النسخ الذكي ---
    const copyBtn = document.createElement('button');
    copyBtn.innerHTML = '📋 نسخ رابط البوستر';
    copyBtn.style = `
        position: fixed; top: 20px; left: 20px; z-index: 10000;
        padding: 12px 20px; background: #27ae60; color: white;
        border: none; border-radius: 50px; cursor: pointer;
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-weight: bold; box-shadow: 0 10px 20px rgba(0,0,0,0.4);
        transition: all 0.3s;
    `;

    copyBtn.onclick = function () {
        navigator.clipboard.writeText(poster).then(() => {
            copyBtn.innerHTML = '✅ تم النسخ!';
            copyBtn.style.background = '#f1c40f';
            copyBtn.style.color = '#000';
            setTimeout(() => copyBtn.remove(), 2500);
        });
    };

    document.body.appendChild(copyBtn);

    // طباعة البيانات في الكونسول للتأكيد
    console.log("%c🔥 تم استخراج البيانات بنجاح:", "color: #f1c40f; font-size: 20px;");
    console.table({ "العنوان": title, "القصة": story, "رابط البوستر": poster });
})();




// ##################################################
//  (Stardust, ReelShort, ShortTV)، استخراج الصورة والاسم والعنوان  
// ##################################################

(function () {
    // 1. استخراج العنوان
    const title = document.querySelector('h1.title')?.innerText?.trim() ||
        document.querySelector('h1')?.innerText?.trim() ||
        document.querySelector('h3')?.innerText?.replace('حبكة', '').trim() || "عنوان غير معروف";

    // 2. استخراج القصة (الوصف)
    const story = document.querySelector('.description-clamp')?.innerText?.trim() ||
        document.querySelector('.rich-text')?.innerText?.trim() ||
        Array.from(document.querySelectorAll('div, p'))
            .find(el => el.innerText.length > 50 && el.innerText.includes(' '))?.innerText?.trim() || "وصف غير معروف";

    // 3. استخراج رابط الصورة (البوستر) بأعلى جودة
    let poster = "";
    const imgEl = document.querySelector('.cover-image img') ||
        document.querySelector('div[role="cover"] img[srcset]') ||
        document.querySelector('img[alt="poster"]') ||
        document.querySelector('img[alt*="' + title.slice(0, 5) + '"]');

    if (imgEl) {
        let rawSrc = "";
        // لو فيه srcset بنسحب أول رابط قبل أول مسافة
        if (imgEl.srcset) {
            rawSrc = imgEl.srcset.trim().split(' ')[0].replace(',', '');
        } else {
            rawSrc = imgEl.src;
        }

        // تنظيف الرابط من أي فلاتر (OSS أو Next.js أو Process)
        if (rawSrc.includes('url=')) {
            poster = decodeURIComponent(rawSrc.split('url=')[1].split('&')[0]);
        } else {
            // الضربة القاضية: بياخد اللي قبل علامة الاستفهام أو علامة الـ @ لو موجودة
            poster = rawSrc.split('?')[0].split('@')[0];
        }
    }

    // --- إنشاء لوحة التحكم (UI) ---
    const container = document.createElement('div');
    container.id = 'eslam-grabber-panel';
    container.style = `
        position: fixed; top: 15px; left: 15px; z-index: 10000;
        display: flex; flex-direction: column; gap: 8px;
        background: #111218; padding: 12px; border-radius: 10px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.7); border: 1px solid #333;
        width: 220px; font-family: 'Segoe UI', Tahoma, sans-serif;
    `;

    function createBtn(text, data, color) {
        const btn = document.createElement('button');
        btn.innerText = text;
        btn.style = `
            padding: 8px 12px; background: ${color}; color: white;
            border: none; border-radius: 5px; cursor: pointer;
            font-size: 13px; font-weight: 600; transition: 0.2s;
            text-align: left; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
        `;
        btn.onclick = function () {
            if (!data || data === "عنوان غير معروف" || data === "وصف غير معروف") {
                btn.innerText = '❌ لم يتم العثور!';
                setTimeout(() => btn.innerText = text, 1500);
                return;
            }
            navigator.clipboard.writeText(data).then(() => {
                const originalText = btn.innerText;
                btn.innerText = '✅ تم النسخ!';
                setTimeout(() => btn.innerText = originalText, 1500);
            });
        };
        return btn;
    }

    // إضافة زر إغلاق
    const header = document.createElement('div');
    header.innerHTML = `<span style="color: #666; font-size: 10px; font-weight: bold;">ESLAM GRABBER V3</span><span style="color: white; float: right; cursor: pointer;">✕</span>`;
    header.style = "margin-bottom: 5px;";
    header.lastChild.onclick = () => container.remove();
    container.appendChild(header);

    // إضافة الأزرار
    container.appendChild(createBtn('🏷️ العنوان', title, '#2980b9'));
    container.appendChild(createBtn('📝 القصة', story, '#8e44ad'));
    container.appendChild(createBtn('🖼️ رابط الصورة', poster, '#d35400'));

    document.body.appendChild(container);

    console.log("🚀 لوحة النسخ جاهزة لـ ShortTV!");
})();