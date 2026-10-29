<!-- Resource Steward local workflow -->
## Resource Steward — سياسة الموارد لهذه المساحة
- عند بدء مهمة جديدة: شغّل `~/bin/resource-steward status` وراجع `~/bin/resource-steward queue`.
- قبل البناء أو التحليل الثقيل: شغّل `~/bin/resource-steward preflight`. شغّل الأعمال التي تتحكم بها عبر `~/bin/resource-steward run -- <command>` حيث يناسب؛ صمّم نقاط تقدم منفصلة للتحليلات الطويلة.
- الخدمة الخلفية اختيارية ومؤقتة: `~/bin/resource-steward daemon --interval 15` باستعمال أداة عمليات الخلفية؛ لا تفترض أنها بقيت حية بعد إعادة بيئة Arena.
- لا تحفظ ملفًا مهمًا كبيرًا في `.cache` وحده. عند ظهور ملف في الطابور، ارفعه عبر موصل Google Drive فقط عند توفره، وتحقق من معرّفه وحجمه قبل تسجيله؛ لا تحذف ملفات المستخدم الأصلية آليًا.
- هذه تعليمات عمل محلية، وليست تكاملًا أصليًا يفرض نفسه قبل كل أداة في Arena. اختبار رفع/استرجاع 110 MB الحقيقي غير مُنجز.
<!-- End Resource Steward local workflow -->
