const API_URL = (import.meta.env.VITE_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

async function parseResponse(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.errors?.[0] || payload?.executive_summary || `The API returned HTTP ${response.status}.`);
  }
  return payload;
}

export async function login({ username, password }) {
  const response = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return parseResponse(response);
}

export async function signup({ username, password }) {
  const response = await fetch(`${API_URL}/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return parseResponse(response);
}

export async function askQuestion({ question, userId, chatId, accessToken, documentContext }) {
  const response = await fetch(`${API_URL}/answer`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      question,
      user_id: userId,
      chat_id: chatId,
      top_k: 5,
      document_context: documentContext || undefined,
    }),
  });
  return parseResponse(response);
}

export async function analyzeMarketIntelligence({
  objective,
  userId,
  companyName,
  companyUrl,
  chatId,
  accessToken,
  documentContext,
}) {
  const response = await fetch(`${API_URL}/agents/market-intelligence`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      objective,
      user_id: userId,
      company_name: companyName || undefined,
      company_url: companyUrl || undefined,
      chat_id: chatId,
      top_k: 5,
      document_context: documentContext || undefined,
    }),
  });
  return parseResponse(response);
}

export async function createMarketStrategy({
  objective,
  userId,
  chatId,
  accessToken,
  documentContext,
}) {
  const response = await fetch(`${API_URL}/agents/market-strategy`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      objective,
      user_id: userId,
      chat_id: chatId,
      top_k: 5,
      document_context: documentContext || undefined,
    }),
  });
  return parseResponse(response);
}

export async function prepareMeeting({
  title,
  objective,
  attendeeContext,
  product,
  industry,
  geography,
  budget,
  keyCompetitors,
  timeline,
  companyName,
  companyUrl,
  userId,
  chatId,
  accessToken,
  documentContext,
}) {
  const response = await fetch(`${API_URL}/agents/meeting-preparation`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      title,
      objective,
      attendee_context: attendeeContext || undefined,
      product: product || undefined,
      industry: industry || undefined,
      geography: geography || undefined,
      budget: budget || undefined,
      key_competitors: keyCompetitors || undefined,
      timeline: timeline || undefined,
      company_name: companyName || undefined,
      company_url: companyUrl || undefined,
      user_id: userId,
      chat_id: chatId,
      top_k: 5,
      document_context: documentContext || undefined,
    }),
  });
  return parseResponse(response);
}

export async function followUpMeeting({
  objective,
  userId,
  projectId,
  chatId,
  accessToken,
  documentContext,
}) {
  const response = await fetch(`${API_URL}/agents/meeting-follow-up`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      objective,
      user_id: userId,
      project_id: projectId || undefined,
      chat_id: chatId,
      top_k: 5,
      document_context: documentContext || undefined,
    }),
  });
  return parseResponse(response);
}

export async function listChats({ accessToken }) {
  const response = await fetch(`${API_URL}/chats`, { headers: { Authorization: `Bearer ${accessToken}` } });
  return parseResponse(response);
}

export async function getChat({ chatId, accessToken }) {
  const response = await fetch(`${API_URL}/chats/${encodeURIComponent(chatId)}`, { headers: { Authorization: `Bearer ${accessToken}` } });
  return parseResponse(response);
}

export async function listPresentationTemplates({ accessToken }) {
  const response = await fetch(`${API_URL}/presentations/templates`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return parseResponse(response);
}

export async function generatePresentation({ chatId, templateId, slideCount, accessToken }) {
  const response = await fetch(`${API_URL}/presentations/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({
      chat_id: chatId,
      template_id: templateId,
      slide_count: slideCount ?? null,
    }),
  });
  return parseResponse(response);
}

export async function getPresentationStatus({ taskId, accessToken }) {
  const response = await fetch(`${API_URL}/presentations/generate/${encodeURIComponent(taskId)}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return parseResponse(response);
}

async function getPresentationArtifact({ taskId, accessToken, suffix }) {
  const response = await fetch(`${API_URL}/presentations/generate/${encodeURIComponent(taskId)}/${suffix}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch { payload = null; }
    throw new Error(payload?.detail || `The API returned HTTP ${response.status}.`);
  }
  const contentDisposition = response.headers.get("Content-Disposition") || "";
  const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: filenameMatch?.[1] || "meeting-presentation.pptx",
  };
}

export function downloadPresentation({ taskId, accessToken }) {
  return getPresentationArtifact({ taskId, accessToken, suffix: "download" });
}

export function previewPresentation({ taskId, accessToken }) {
  return getPresentationArtifact({ taskId, accessToken, suffix: "preview" });
}

export async function deleteChat({ chatId, accessToken }) {
  const response = await fetch(`${API_URL}/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return parseResponse(response);
}

export async function forkChat({ chatId, accessToken }) {
  const response = await fetch(`${API_URL}/chats/${encodeURIComponent(chatId)}/fork`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
  });
  return parseResponse(response);
}

export async function uploadDocument({ file, userId, accessToken }) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("user_id", userId);
  const response = await fetch(`${API_URL}/ingest`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: formData,
  });
  return parseResponse(response);
}

export async function getIngestionStatus({ jobId, accessToken }) {
  const response = await fetch(`${API_URL}/ingest/${encodeURIComponent(jobId)}`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  return parseResponse(response);
}


export async function extractChatDocument({ file, accessToken }) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_URL}/chat/extract-document`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body: formData,
  });
  return parseResponse(response);
}
