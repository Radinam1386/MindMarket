# دیپلوی روی VPS اختصاصی — راهنمای بهره‌برداری

این سند جایگزین مسیر لیارا برای استقرار روی سرور اختصاصی است
(`62.60.194.78` — Ubuntu 24.04، Docker + Compose). معاملهٔ واقعی وجود ندارد؛ فقط paper.

## معماری

```
VPS 62.60.194.78
 └─ /opt/marketmind            کلون ریپو (main)
     ├─ compose.yml            تعریف سرویس marketmind (پورت 8787)
     ├─ deploy/vps-deploy.sh   اسکریپت pull-deploy (fetch → build → up → healthcheck)
     ├─ deploy/systemd/        یونیت‌های تایمر CD (هر ۵ دقیقه)
     └─ data/                  دادهٔ ماندگار ( volume ./data:/app/data )
         ├─ paper.sqlite       حساب اصلی، موقعیت‌ها و تصمیم‌ها
         ├─ accounts/          دفتر مستقل حساب‌های دیگر
         ├─ candles/           کندل‌ها (بوت اول از seed پر می‌شود، بعد از API تازه می‌شود)
         └─ secrets/           dashboard_users.json · dashboard_session.key · llm_api.json · telegram.json (اختیاری)
```

- **داشبورد:** `http://62.60.194.78:8787` — صفحهٔ اصلی عمومی است؛ هر حساب از `/login` وارد فضای مستقل خود می‌شود.
- گذرواژه‌ها به‌صورت هش در `data/secrets/dashboard_users.json` ذخیره می‌شوند. حساب اصلی همچنان از `paper.sqlite` استفاده می‌کند؛ برای هر حساب دیگر یک فایل مستقل در `data/accounts/` وجود دارد.
- در نخستین اجرای نسخهٔ جدید، حساب قدیمی از `dashboard_login.json` یا متغیرهای `DASHBOARD_USER` و `DASHBOARD_PASSWORD` وارد می‌شود، بدون تغییر دفتر معاملات. پس از بررسی ورود، فایل قدیمی حاوی گذرواژهٔ متنی را حذف کنید.
- هر حساب چرخه و تنظیمات جدا دارد. `MARKETMIND_SCHEDULER=1` برای اجرای خودکار حساب‌ها لازم است؛ فعال بودن چرخه به‌تنهایی کافی نیست.
- نشانی فعلی HTTP است. برای محافظت از اعتبارنامه‌ها در شبکه، HTTPS را با نام دامنه و پروکسی معکوس فراهم کنید.

### مدیریت حساب‌ها

```bash
# دستورها رمز را به‌صورت تعاملی دریافت می‌کنند؛ رمز را در آرگومان ننویسید.
docker compose exec marketmind python -m marketmind.accounts set-owner USERNAME
docker compose exec marketmind python -m marketmind.accounts set-user USERNAME --initial-cash 200 --auto-trade
```

تکرار دستور برای یک کاربر رمز او را عوض می‌کند و موجودی و تاریخچه را بازنشانی نمی‌کند. `--auto-trade` فقط هنگام ساخت حساب جدید اعمال می‌شود؛ بعداً آن را از داشبورد کنترل کنید.

## جریان انتشار (CI/CD)

1. push به `main` → گیت‌هاب اکشن تست‌ها را اجرا می‌کند (`.github/workflows/cicd.yml`).
2. **مسیر اصلی CD:** تایمر `marketmind-deploy.timer` روی سرور هر ۵ دقیقه
   `deploy/vps-deploy.sh` را صدا می‌زند؛ اگر `origin/main` جلو رفته باشد،
   pull + build + restart می‌کند و `/healthz` را چک می‌کند. یعنی حداکثر ~۵ دقیقه
   بعد از push سبز، نسخهٔ جدید روی سرور است — بدون هیچ سکریتی در گیت‌هاب.
3. **مسیر اختیاری (دیپلوی آنی):** اگر سکرت‌های `VPS_HOST` و `VPS_SSH_KEY`
   در Settings → Secrets → Actions ریپو ست شوند، جاب `deploy-vps` بلافاصله
   بعد از تست سبز، همان اسکریپت را با `--force` از طریق SSH اجرا می‌کند.
4. دیپلوی داده‌ها را دست نمی‌زند — فقط کد/ایمیج عوض می‌شود (`data/` volume است).

## معامله‌گری پیوسته (حالت فعال)

- در داشبورد (نمای کل → کنترل): دکمهٔ **«🔁 پیوسته»** — وقتی روشن باشد، هر
  `MARKETMIND_CYCLE_MINUTES` دقیقه (پیش‌فرض ۱۵) چرخهٔ کامل اجرا می‌شود:
  به‌روزرسانی کندل‌های ۱روزه/۴ساعته/۱ساعته + تصمیم‌گیری + سفارش کاغذی.
- تا وقتی خودتان خاموش نکنید روشن می‌ماند (تنظیم در دیتابیس است و بعد از ری‌استارت هم می‌ماند).
- دروازه‌های ورود روی سرور با `/opt/marketmind/.env` شل‌تر شده‌اند (فقط paper؛ مقادیر تحقیقاتی ریپو دست‌نخورده‌اند):
  `MARKETMIND_ER_MIN=0.15` · `MARKETMIND_LLM_CONF_MIN=0.55` · `MARKETMIND_CYCLE_MINUTES=15`
  · `MARKETMIND_ALLOW_SHORTS=1` (پوزیشن شورت فعال — سود از ریزش بازار نزولی)
  · `MARKETMIND_MIN_NOTIONAL=25` (حداقل ارزش پوزیشن در حالت دمو؛ پیش‌فرض ۱۰۰ مثل صرافی)
  · `MARKETMIND_FRACTION_OF_CASH=0.30` (سقف وثیقه هر معامله؛ پیش‌فرض ۰٫۲۰)
  · `MARKETMIND_VOL_TARGET_RISK=0.05` (ریسک هدف هر معامله؛ پیش‌فرض ۰٫۰۲ — اهرم سایز)
  · `MARKETMIND_MIN_SIZE_FACTOR=0.6` (کف ضریب کوچک‌سازی AI؛ پیش‌فرض ۰ یعنی نظر AI کامل اعمال شود)
  · `MARKETMIND_STOP_ATR=2.0` (حد ضرر هر پوزیشن = ۲ برابر ATR لحظهٔ ورود؛ ۰ = خاموش)
  · `MARKETMIND_TRAIL_TRIGGER_ATR=1.0` (اگر پوزیشن ۱ برابر ATR سود کند، حد ضرر می‌آید روی نقطهٔ ورود — سود قفل می‌شود)
  · `MARKETMIND_SIGNAL_MODE=closed` (سیگنال روی کندل بسته‌شده — هم‌تراز با بک‌تست و قانون طلایی ۴؛ "live" = کندل در حال ساخت)
  · `MARKETMIND_BTC_FILTER_SHORTS=1` (شورت فقط وقتی BTC زیر SMA200 خودش باشد — ضد شورت خلاف‌جهت)
  · `MARKETMIND_MAX_SAME_SIDE=2` (حداکثر پوزیشن هم‌جهت — ضد همبستگی)
- دلیل «وارد نشدن» هر کوین در جریان تصمیم‌ها ثبت می‌شود (ER فعلی، حد مجاز،
  موقعیت نسبت به SMA200، وضعیت شکست کانال) و در صفحهٔ 📜 لاگ دیده می‌شود.

## دستورهای روزمره

```bash
ssh root@62.60.194.78

cd /opt/marketmind
docker compose ps                       # وضعیت
docker compose logs -f --tail=100       # لاگ زنده
systemctl list-timers | grep marketmind # تایمر CD
/opt/marketmind/deploy/vps-deploy.sh --force   # دیپلوی دستی/فوری
docker compose restart                  # ری‌استارت ساده
```

رول‌بک: `cd /opt/marketmind && git reset --hard <sha قبلی> && docker compose build && docker compose up -d`

## نکته‌های شبکه (سرور داخل ایران)

- **Docker Hub مستقیم بلاک است** (403 از CDN). آینه‌ها در `/etc/docker/daemon.json`
  تنظیم شده‌اند؛ اگر روزی خراب شد، فقط همان فایل را عوض کنید و `systemctl restart docker`.
- PyPI داخل Dockerfile اول از آینهٔ runflare می‌رود و به pypi.org fallback می‌کند.
- GitHub و Wallex از سرور در دسترس‌اند.
- **تلگرام از IP ایران بلاک است** → یا `telegram.json` نگذارید (بی‌صدا غیرفعال می‌شود)،
  یا فیلد `base_url` را به یک پروکسی ست کنید.

## اسرار

هیچ کلیدی در گیت/ایمیج نیست. روی سرور در `/opt/marketmind/data/secrets/`:
- `dashboard_users.json` و `dashboard_session.key` — هش اعتبارنامه‌ها و کلید نشست
- `llm_api.json` — کانفیگ LLM (کپی از لوکال؛ بدون آن پنل AI «تحلیل ناموفق» می‌دهد ولی سیستم بالا می‌ماند)
- `telegram.json` — اختیاری (بلاک بودن تلگرام از ایران را ببینید)

اعتبارنامهٔ قدیمی داشبورد برای مهاجرت اولیه و LLM/تلگرام از env هم خوانده می‌شوند (`DASHBOARD_USER`,
`DASHBOARD_PASSWORD`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) — فایل در `/opt/marketmind/.env`
(کامیت نمی‌شود) و compose آن را به کانتینر می‌دهد.

## عیب‌یابی سریع

| نشانه | علت احتمالی | راه‌حل |
|---|---|---|
| ورود رد می‌شود | یوزر/پس غلط | با دستور مدیریت حساب، رمز را دوباره تنظیم کنید |
| حسابی برای ورود وجود ندارد | فایل کاربران ایجاد نشده | با دستور `set-owner` حساب اصلی را بسازید |
| build خطای pull از docker.io | آینه‌ها موقتاً خراب | `daemon.json` را عوض کنید یا بعداً تایمر خودش دوباره امتحان می‌کند |
| AI همیشه «تحلیل ناموفق» | `llm_api.json` نیست یا LLM در دسترس نیست | فایل را در `data/secrets/` بگذارید و ری‌استارت |
| چرخهٔ روزانه اجرا نمی‌شود | `MARKETMIND_SCHEDULER` حذف شده | در compose.yml باید `1` باشد؛ لاگ را ببینید |
| دکمهٔ «اجرای چرخهٔ الان» کاری نمی‌کند | حساب HALTED است یا چرخهٔ دیگری در جریان است | دلیل بلافاصله روی دکمه نشان داده می‌شود؛ جزئیات کامل در صفحهٔ 📜 لاگ داشبورد |
| دیپلوی خودکار انجام نمی‌شود | تایمر مرده یا خطای بیلد | `systemctl status marketmind-deploy` و `journalctl -u marketmind-deploy -n 100` |
