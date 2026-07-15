(function () {
    // 1. إعدادات الحماية والمنع
    console.clear = () => console.log("%c [!] الموقع حاول مسح الكونسول.", "color: orange;");
    window.onbeforeunload = () => "تحذير!";

    // 2. إنشاء واجهة تحكم بسيطة في الصفحة (Floating UI)
    const ui = document.createElement('div');
    ui.id = 'hunter-ui';
    ui.innerHTML = `
        <div
            style="position:fixed; top:10px; left:10px; z-index:999999; background:#1e1e2e; color:white; padding:15px; border-radius:10px; font-family:sans-serif; box-shadow:0 4px 15px rgba(0,0,0,0.5); border:1px solid #444; min-width:250px;">
            <h4
                style="margin:0 0 10px 0; color:#00f2fe; font-size:14px; display:flex; justify-content:space-between; align-items:center;">
                🎯 صائد المشغلات الذكي
                <span onclick="document.getElementById('links-container').innerHTML=''; "
                    style="cursor:pointer; font-size:12px;" title="مسح القائمة">🗑️</span>
            </h4>
            <div id="stats" style="font-size:12px; margin-bottom:10px; color:#aaa;">جاري البحث عن روابط...</div>
            <div id="links-container" style="max-height:200px; overflow-y:auto; scrollbar-width: thin;"></div>
            <div style="display:flex; gap:5px; margin-top:10px;">
                <button onclick="window.stopHunting()"
                    style="flex:1; background:#ff4b2b; color:white; border:none; padding:7px; border-radius:5px; cursor:pointer; font-weight:bold; font-size:11px;">إيقاف</button>
                <button onclick="location.reload()"
                    style="flex:1; background:#555; color:white; border:none; padding:7px; border-radius:5px; cursor:pointer; font-weight:bold; font-size:11px;">تحديث</button>
            </div>
        </div>
    `;
    document.body.appendChild(ui);

    const foundLinks = new Set();
    const linksContainer = document.getElementById('links-container');
    const stats = document.getElementById('stats');

    // وظيفة فتح المعاينة
    window.previewLink = (url) => {
        window.open(url, 'p-window', 'width=800,height=450,menubar=no,toolbar=no');
    };

    // 3. وظيفة النسخ الذكي
    window.copyToClipboard = (text, btn) => {
        navigator.clipboard.writeText(text).then(() => {
            btn.innerText = '✅';
            btn.style.background = '#2ecc71';
            setTimeout(() => {
                btn.innerText = 'Copy';
                btn.style.background = '#3498db';
            }, 1000);
        });
    };

    // 4. مهمة البحث المتكرر
    const huntTask = setInterval(() => {
        const frames = document.querySelectorAll('iframe');
        const videos = document.querySelectorAll('video source, video');
        const allElements = [...frames, ...videos];

        allElements.forEach(el => {
            try {
                let src = el.src || el.getAttribute('data-src') || el.getAttribute('data-lazy-src') || el.currentSrc;

                if (src) {
                    const absoluteSrc = new URL(src, window.location.origin).href;
                    const junk = ['google', 'facebook', 'doubleclick', 'ads', 'analytics', 'twitter'];
                    const isJunk = junk.some(j => absoluteSrc.toLowerCase().includes(j));

                    if (!isJunk && absoluteSrc !== window.location.href && !foundLinks.has(absoluteSrc)) {
                        let finalUrl = absoluteSrc;
                        foundLinks.add(finalUrl);

                        const time = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        
                        const linkRow = document.createElement('div');
                        linkRow.style = "margin-bottom:10px; background:#2a2a3d; padding:8px; border-radius:6px; border-left:3px solid #00f2fe;";
                        linkRow.innerHTML = `
                            <div style="font-size:9px; color:#888; margin-bottom:4px;">⏱️ ${time}</div>
                            <div style="font-size:10px; color:#ccc; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:6px;">${finalUrl.split('?')[0]}</div>
                            <div style="display:flex; gap:4px;">
                                <button onclick="window.copyToClipboard('${finalUrl}', this)" style="flex:1; background:#3498db; color:white; border:none; padding:4px; border-radius:3px; font-size:10px; cursor:pointer;">Copy</button>
                                <button onclick="window.previewLink('${finalUrl}')" style="flex:1; background:#9b59b6; color:white; border:none; padding:4px; border-radius:3px; font-size:10px; cursor:pointer;">▶ View</button>
                            </div>
                        `;
                        linksContainer.prepend(linkRow); // يضع الرابط الجديد في الأعلى

                        stats.innerText = `تم صيد (${foundLinks.size}) روابط`;
                    }
                }
            } catch (e) { }
        });
    }, 1000);

    window.stopHunting = () => {
        clearInterval(huntTask);
        ui.style.opacity = '0.7';
        stats.innerText = "🛑 تم إيقاف البحث.";
    };
})();
