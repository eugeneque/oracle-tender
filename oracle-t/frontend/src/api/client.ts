const TOKEN_STORAGE_KEY = "oraclet_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setToken(token: string | null): void {
  if (token) {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`/api${path}`, { ...options, headers });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // тело ответа не JSON — оставляем statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: "GET" }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

/**
 * Отправляет файл как multipart/form-data (CSV-импорт каталога). Content-Type здесь НЕ
 * выставляется вручную: браузер должен сам добавить boundary, иначе сервер не разберёт тело.
 */
export async function uploadFile<T>(path: string, file: File): Promise<T> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const form = new FormData();
  form.append("file", file);

  const response = await fetch(`/api${path}`, { method: "POST", headers, body: form });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // тело не JSON — оставляем statusText
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/**
 * Отправляет произвольную форму как multipart/form-data — поля и несколько файлов сразу
 * (ручная заявка, загрузка документов к тендеру). Отдельно от `uploadFile`: тот собирает
 * форму из одного файла сам, а здесь её собирает вызывающий код. Content-Type так же не
 * выставляется — boundary добавит браузер.
 */
export async function postForm<T>(path: string, form: FormData): Promise<T> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`/api${path}`, { method: "POST", headers, body: form });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      // FastAPI отдаёт ошибки валидации формы списком — показываем первую человеку.
      detail = Array.isArray(body.detail)
        ? body.detail[0]?.msg ?? detail
        : body.detail ?? detail;
    } catch {
      // тело не JSON — оставляем statusText
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/**
 * Скачивает файл, отдаваемый API (выгрузка в Excel, раздел 5.7 ТЗ).
 *
 * Через fetch, а не `window.open`/`<a href>`: эндпоинт закрыт Bearer-токеном, который в
 * обычной навигации браузера не отправляется. Ответ материализуется в blob и «кликается»
 * временной ссылкой — иначе браузер отрисовал бы бинарник как страницу.
 *
 * Имя файла берётся из Content-Disposition: его формирует сервер (там же дата и период
 * выгрузки), и дублировать эту логику на клиенте значило бы получить два разных имени.
 */
export async function downloadFile(
  path: string,
  fallbackName: string,
): Promise<{ fileName: string; rows: number | null }> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`/api${path}`, { method: "GET", headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // тело ответа не JSON — оставляем statusText
    }
    throw new ApiError(response.status, detail);
  }

  const disposition = response.headers.get("Content-Disposition") ?? "";
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const plainMatch = disposition.match(/filename="([^"]+)"/i);
  const fileName = utf8Match
    ? decodeURIComponent(utf8Match[1])
    : plainMatch?.[1] ?? fallbackName;

  const rowsHeader = response.headers.get("X-Exported-Rows");
  const blob = await response.blob();

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Освобождаем object URL не сразу: Safari успевает начать скачивание не мгновенно, и
  // немедленный revoke иногда обрывал загрузку на пустом файле.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);

  return { fileName, rows: rowsHeader ? Number(rowsHeader) : null };
}
