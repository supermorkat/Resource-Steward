# Resource Steward — التثبيت في جلسة Arena أخرى

هذه الحزمة تحتوي الشيفرة والاختبارات ودليل الاستخدام، **ولا تحتوي بيانات الحالة (`state.sqlite`)، السجلات، مفاتيح Google، ملفات مؤقتة أو ملفات Superpowers نفسها**.

## التثبيت

بعد تنزيل ZIP إلى جلسة أخرى:

```bash
mkdir -p /home/user/.cache/resource-steward-install
unzip -q Resource-Steward-2026-10-29.zip -d /home/user/.cache/resource-steward-install
sh /home/user/.cache/resource-steward-install/Resource-Steward-2026-10-29/install.sh --enable-agent-guide
python3 -m unittest discover -s /home/user/resource-steward/tests -v
/home/user/bin/resource-steward status
```

- الخيار `--enable-agent-guide` **يضيف** تعليمات موجزة إلى `~/AGENTS.md` مرة واحدة دون مسح محتواه؛ يمكن حذفه إذا لم ترد تعديل ملف الإرشادات. وجود الإرشادات لا يعني أن Arena يقرأها تلقائيًا.
- المثبّت يُحدّث ملفات الشيفرة فقط؛ لا ينسخ أو يمسح قاعدة الحالة الموجودة في جلسة الهدف. شغّل المراقبة بأداة العمليات الخلفية المناسبة في الجلسة: `/home/user/bin/resource-steward daemon --interval 15`. المثبّت **لا يبدأ** خدمة دائمة بنفسه.
- استعمل `preflight` قبل المهام الثقيلة و`run -- <command>` للأعمال المحروسة. راجع `resource-steward/README.md` لمخاطر `run` وحدود الحماية.
- هذه الأداة **لا ترفع تلقائيًا إلى Drive**. عند ظهور ملف في `queue`، ينفّذ المساعد الرفع عبر موصل Drive إن كان متصلًا بالجلسة ويؤكد النتائج؛ ليس هناك اعتماد Drive داخل ZIP.
- حجم **110 MB** كان معيار اختبار محلي sparse فقط؛ **رفع/استرجاع 110 MB عبر Drive غير مختبر**.

يمكن تثبيتها أيضًا في مسار منزل بديل بتعيين `RESOURCE_STEWARD_HOME=/absolute/path` عند تشغيل `install.sh`، لكن إعدادات Arena المقصودة تستخدم `/home/user`.
