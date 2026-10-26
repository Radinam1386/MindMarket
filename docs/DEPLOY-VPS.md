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
         ├─ paper.sqlite       حساب ۲۰۰ USDT و موقعیت‌ها و تصمیم‌ها
         ├─ candles/           کندل‌ها (بوت اول از seed پر می‌شود، بعد از API تازه می‌شود)
         └─ secrets/           dashboard_login.json · llm_api.json · telegram.json (اختیاری)
```

- **داشبورد:** `http://62.60.194.78:8787` — ورود با **یوزر/پس** (پنجرهٔ استاندارد Basic Auth مرورگر).
  اعتبارنامه‌ها در `data/secrets/dashboard_login.json`:
  `{"username": "...", "password": "..."}` — تغییرشان بدهید و `docker compose restart` بزنید.
- بدون اعتبارنامه: درخواست ریموت ۴۰۱ می‌گیرد؛ اگر فایل ورود اصلاً نباشد ۴۰۳.
  فقط `127.0.0.1` (روی خود سرور) و `/healthz` بدون ورود بازند.
- توکن در URL دیگر وجود ندارد (حذف شد)؛ `dashboard_token.txt` قدیمی نادیده گرفته می‌شود.
- چرخهٔ روزانهٔ paper داخل کانتینر اجرا می‌شود (`MARKETMIND_SCHEDULER=1`)، هر روز ۰۱:۰۰ UTC = ۰۴:۳۰ تهران.

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
- `dashboard_login.json` — یوزر/پس داشبورد (ورود مرورگر)
- `llm_api.json` — کانفیگ LLM (کپی از لوکال؛ بدون آن پنل AI «تحلیل ناموفق» می‌دهد ولی سیستم بالا می‌ماند)
- `telegram.json` — اختیاری (بلاک بودن تلگرام از ایران را ببینید)

اعتبارنامهٔ داشبورد و LLM/تلگرام از env هم خوانده می‌شوند (`DASHBOARD_USER`,
`DASHBOARD_PASSWORD`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) — فایل در `/opt/marketmind/.env`
(کامیت نمی‌شود) و compose آن را به کانتینر می‌دهد.

## عیب‌یابی سریع

| نشانه | علت احتمالی | راه‌حل |
|---|---|---|
| پنجرهٔ ورود باز می‌شود و رد می‌شوید | یوزر/پس غلط | محتوای `data/secrets/dashboard_login.json`؛ مرورگر را ببندید و باز کنید (کش اعتبارنامه) |
| 403 ریموت با پیام «login not configured» | `dashboard_login.json` نیست | فایل را در `data/secrets/` بسازید و `docker compose restart` |
| build خطای pull از docker.io | آینه‌ها موقتاً خراب | `daemon.json` را عوض کنید یا بعداً تایمر خودش دوباره امتحان می‌کند |
| AI همیشه «تحلیل ناموفق» | `llm_api.json` نیست یا LLM در دسترس نیست | فایل را در `data/secrets/` بگذارید و ری‌استارت |
| چرخهٔ روزانه اجرا نمی‌شود | `MARKETMIND_SCHEDULER` حذف شده | در compose.yml باید `1` باشد؛ لاگ را ببینید |
| دیپلوی خودکار انجام نمی‌شود | تایمر مرده یا خطای بیلد | `systemctl status marketmind-deploy` و `journalctl -u marketmind-deploy -n 100` |
