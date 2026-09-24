# Дашборд Kaspi Repricer

Next.js (App Router) + Tailwind + lucide-react поверх FastAPI-бэкенда из этого репозитория.

## Запуск

Сначала бэкенд:

```bash
alembic upgrade head
KASPI_MERCHANT_ID=... KASPI_COMPANY='ТОО "Ромашка"' \
DATABASE_URL=postgresql+psycopg://... uvicorn repricer.api.app:app --port 8000
```

Затем дашборд:

```bash
cd frontend
npm install
cp .env.example .env.local        # при необходимости поправьте API_URL
npm run dev                       # http://localhost:3000
```

`API_URL` читается **в рантайме**: и серверными компонентами, и прокси
`/api/*` в `src/app/api/[...path]/route.ts`. Браузер ходит только на origin
дашборда, поэтому CORS на FastAPI настраивать не нужно.

## Что внутри

| Путь | Назначение |
| --- | --- |
| `src/app/page.tsx` | Серверный компонент: грузит правила и города |
| `src/components/ProductsTable.tsx` | Таблица товаров, фильтр по городу, пагинация |
| `src/components/StrategyDialog.tsx` | Модалка стратегий, городов, белого списка и лимитов |
| `src/app/api/[...path]/route.ts` | Прокси к FastAPI |
| `src/lib/client.ts` | Мутации: одно окно настроек → вызовы по каждому городу |

## Чего нет

- **Автотестов.** Бэкенд закрыт pytest, фронтенд проверялся сборкой, линтером и
  запуском против живого API.
- **Аутентификации.** Её нет и в API: перед выкладкой наружу закройте обе части.
- Стратегии `fixed_price` и `manual` поддерживаются API, но в интерфейсе не
  показаны — в ТЗ их нет.
