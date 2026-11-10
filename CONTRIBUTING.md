# Учёт объектов — правила работы

## еплой
еред каждым деплоем поднимай версию (видна в шапке сайта как v4.N):

Clear-Host; Set-Location 'E:\Projects\accounting-of-objects'; .\bump.ps1 "что изменил"; git push origin main; railway up --detach

## равила для -агента
- равки HTML делай в templates/ (корневая) — она копируется в Docker. deploy/templates/ — устаревшая копия,  трогай её.
- Ставка рабочего: workers.daily_rate.  worker_assignments.total_pay хранится снимок; при смене ставки пересчитывается через UPDATE worker_assignments SET total_pay = daily_rate * days_worked.
- ерсия: FINANCIAL_LOGIC_VERSION в app_objects.py. ампается через .\bump.ps1.
- ириллица в PowerShell ломается. спользуй Sublime Text или [System.IO.File]::WriteAllText с UTF8Encoding(false).
- дна команда за раз.

## кружение
- роект: E:\Projects\accounting-of-objects (Python + Flask + SQLite/PostgreSQL)
- GitHub: Andrey1803/accounting-of-objects
- Railway: проект «Учот обьектов», сервис «учот обьектов»
- psql: E:\PostgreSQL18\pgsql\bin\psql.exe