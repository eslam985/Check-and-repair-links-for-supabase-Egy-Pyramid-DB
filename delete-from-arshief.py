import internetarchive as ia

ACCESS_KEY = 'ufnS9MloPsaLYXSl'
SECRET_KEY = 'euu3u0Lm0bcMFyYB'
SEARCH_QUERY = 'title:"الجواز السري والحب الخفي الموسم 1 الحلقة 1" AND uploader:ee17172@gmail.com'


session = ia.get_session(config={
    's3': {
        'access': ACCESS_KEY,
        'secret': SECRET_KEY
    }
})

print("جاري البحث عن العناصر...")
search_results = ia.search_items(SEARCH_QUERY, fields=['identifier', 'title'])
items_found = list(search_results)

if not items_found:
    print("لم يتم العثور على أي عناصر.")
else:
    print(f"\nتم العثور على {len(items_found)} عنصر.")
    for i, result in enumerate(items_found, 1):
        title = result.get('title', 'لا يوجد عنوان')
        identifier = result['identifier']
        print(f"{i}- [الاسم: {title}] -> [المعرف: {identifier}]")

    confirm = input("\n⚠️ هل تريد حذف جميع ملفات .mp4 من هذه العناصر نهائياً؟ (اكتب 'y' للموافقة): ")
    if confirm.lower() == 'y':
        print("\nجاري حذف الملفات...")
        
        for result in items_found:
            item_id = result['identifier']
            print(f"\nمعالجة العنصر: {item_id}")
            
            item = session.get_item(item_id)
            mp4_files = [f for f in item.files if f['name'].endswith('.mp4')]
            
            if not mp4_files:
                print("  - لا يوجد ملفات .mp4 في هذا العنصر.")
                continue
            
            for file_dict in mp4_files:
                file_name = file_dict['name']
                print(f"  - حذف: {file_name} ...", end=" ", flush=True)
                
                try:
                    file_obj = item.get_file(file_name)
                    response = file_obj.delete(cascade_delete=True)
                    
                    # ✅ التعديل هنا: اعتبار 200 و 204 نجاحاً
                    if response.status_code in [200, 204]:
                        print("✅ تم الحذف")
                    else:
                        print(f"❌ فشل (رمز {response.status_code})")
                except Exception as e:
                    print(f"❌ خطأ: {e}")
        
        print("\n✅ تم الانتهاء من معالجة جميع العناصر.")
    else:
        print("\nتم إلغاء العملية.")