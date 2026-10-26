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
     └─ data/                  دادهٔ ماندگار ( volumen ./data:/app/data )
         ├─ paper.sqlite       حساب ۲۰۰ USDT و موقعیت‌ها و تصمیم‌ها
         ├─ candles/           کندل‌ها (بوت اول از seed پر می‌شود، بعد از API تازه می‌شود)
         └─ secrets/           llm_api.json · dashboard_token.txt · telegram.json (اختیاری)
```

- داشبورد: `http://62.60.194.78:8787/?token=<محتوای dashboard_token.txt>`
- بدون توکن، دسترسی ریموت ۴۰۳ می‌شود (رفتار عمدی کد).
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
   کلید عمومی CI از قبل روی سرور نصب است؛ کلید خصوصی یک‌بار به‌عنوان
   `VPS_SSH_KEY` وارد می‌شود (فایل `marketmind-ci-ed25519` خارج از ریپو).
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
- `dashboard_token.txt` — توکن داشبورد (تولیدشده هنگام راه‌اندازی)
- `llm_api.json` — کانفیگ LLM (کپی از لوکال؛ بدون آن پنل AI «تحلیل ناموفق» می‌دهد ولی سیستم بالا می‌ماند)
- `telegram.json` — اختیاری (بلاک بودن تلگرام از ایران را ببینید)

LLM/تلگرام/توکن از env هم خوانده می‌شوند (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DASHBOARD_TOKEN`) — فایل در `/opt/marketmind/.env`
(کامیت نمی‌شود) و compose آن را به کانتینر می‌دهد.

## عیب‌یابی سریع

| نشانه | علت احتمالی | راه‌حل |
|---|---|---|
| 401 روی `/` | توکن در URL نیست/غلط است | `?token=` با محتوای `data/secrets/dashboard_token.txt` |
| 403 از لوکال‌هاست سرور | توکن ست نشده | فایل توکن را بسازید و `docker compose restart` |
| build خطای pull از docker.io | آینه‌ها موقتاً خراب | `daemon.json` را عوض کنید یا بعداً تایمر خودش دوباره امتحان می‌کند |
| AI همیشه «تحلیل ناموفق» | `llm_api.json` نیست یا LLM در دسترس نیست | فایل را در `data/secrets/` بگذارید و ری‌استارت |
| چرخهٔ روزانه اجرا نمی‌شود | `MARKETMIND_SCHEDULER` حذف شده | در compose.yml باید `1` باشد؛ لاگ را ببینید |
| دیپلوی خودکار انجام نمی‌شود | تایمر مرده یا خطای بیلد | `systemctl status marketmind-deploy` و `journalctl -u marketmind-deploy -n 100` |
