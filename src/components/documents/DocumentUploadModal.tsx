"use client";

import { useState, useEffect, useRef } from "react";
import {
  AlertCircle,
  CheckCircle2,
  Download,
  File,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
  UploadCloud,
  X,
} from "lucide-react";
import {
  DocumentCategory,
  DocumentMetadata,
  deleteProjectDocument,
  fetchProjectDocuments,
  getDocumentDownloadUrl,
  uploadProjectDocument,
} from "@/lib/prediction-api";

interface DocumentUploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
  projectName: string;
  onDocumentCountChange?: (count: number) => void;
}

export type QueuedFileStatus = "Waiting" | "Uploading" | "Uploaded" | "Failed";

export interface QueuedFileItem {
  id: string;
  file: File;
  name: string;
  extension: string;
  mimeType: string;
  size: number;
  status: QueuedFileStatus;
  errorMessage?: string;
  category: DocumentCategory;
  description?: string;
}

const SUPPORTED_EXTENSIONS = [
  ".pdf",
  ".doc",
  ".docx",
  ".xls",
  ".xlsx",
  ".csv",
  ".jpg",
  ".jpeg",
  ".png",
];

const MAX_FILE_SIZE_MB = 20;
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

const CATEGORIES: DocumentCategory[] = [
  "Detailed Project Report (DPR)",
  "Environmental Clearance",
  "Land Acquisition Record",
  "Financial & Expenditure Report",
  "Site Survey & Geotechnical",
  "Contract & Tender Agreement",
  "Other / Supporting Document",
];

function formatBytes(bytes: number, decimals = 1): string {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

function getFileIcon(filename: string) {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf") return <FileText className="doc-icon-pdf" size={18} />;
  if (["xlsx", "xls", "csv"].includes(ext))
    return <FileSpreadsheet className="doc-icon-excel" size={18} />;
  if (["png", "jpg", "jpeg", "webp"].includes(ext))
    return <ImageIcon className="doc-icon-img" size={18} />;
  return <File className="doc-icon-default" size={18} />;
}

export default function DocumentUploadModal({
  isOpen,
  onClose,
  projectId,
  projectName,
  onDocumentCountChange,
}: DocumentUploadModalProps) {
  const [documents, setDocuments] = useState<DocumentMetadata[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [fileQueue, setFileQueue] = useState<QueuedFileItem[]>([]);
  const [globalCategory, setGlobalCategory] = useState<DocumentCategory>(
    "Detailed Project Report (DPR)"
  );
  const [globalDescription, setGlobalDescription] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [isUploadingAny, setIsUploadingAny] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [generalMessage, setGeneralMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropzoneRef = useRef<HTMLDivElement>(null);

  const loadDocuments = async () => {
    setIsLoading(true);
    setFetchError(null);
    try {
      const res = await fetchProjectDocuments(projectId);
      setDocuments(res.documents);
      onDocumentCountChange?.(res.total_count);
    } catch {
      setFetchError(
        "Unable to load cataloged documents. Please verify your connection."
      );
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void loadDocuments();
      setGeneralMessage(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, projectId]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen && !isUploadingAny) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose, isUploadingAny]);

  if (!isOpen) return null;

  const addFilesToQueue = (files: FileList | File[]) => {
    const newItems: QueuedFileItem[] = [];
    Array.from(files).forEach((file) => {
      const ext = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
      let status: QueuedFileStatus = "Waiting";
      let errorMessage: string | undefined;

      if (!SUPPORTED_EXTENSIONS.includes(ext)) {
        status = "Failed";
        errorMessage = `Unsupported type (${ext}). Allowed: PDF, DOC, DOCX, XLS, XLSX, CSV, JPG, JPEG, PNG.`;
      } else if (file.size > MAX_FILE_SIZE_BYTES) {
        status = "Failed";
        errorMessage = `Exceeds maximum size of ${MAX_FILE_SIZE_MB} MB.`;
      }

      newItems.push({
        id: `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`,
        file,
        name: file.name,
        extension: ext,
        mimeType: file.type || ext,
        size: file.size,
        status,
        errorMessage,
        category: globalCategory,
        description: globalDescription || undefined,
      });
    });
    setFileQueue((prev) => [...prev, ...newItems]);
    setGeneralMessage(null);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files?.length > 0) {
      addFilesToQueue(e.dataTransfer.files);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length > 0) {
      addFilesToQueue(e.target.files);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const removeQueueItem = (id: string) => {
    setFileQueue((prev) => prev.filter((item) => item.id !== id));
  };

  const uploadSingleItem = async (item: QueuedFileItem): Promise<boolean> => {
    setFileQueue((prev) =>
      prev.map((i) =>
        i.id === item.id
          ? { ...i, status: "Uploading", errorMessage: undefined }
          : i
      )
    );
    try {
      await uploadProjectDocument(
        projectId,
        item.file,
        item.category,
        item.description,
        "Ananya Sharma"
      );
      setFileQueue((prev) =>
        prev.map((i) => (i.id === item.id ? { ...i, status: "Uploaded" } : i))
      );
      await loadDocuments();
      return true;
    } catch (err) {
      const errMsg =
        err instanceof Error ? err.message : "Upload failed. Please retry.";
      setFileQueue((prev) =>
        prev.map((i) =>
          i.id === item.id
            ? { ...i, status: "Failed", errorMessage: errMsg }
            : i
        )
      );
      return false;
    }
  };

  const handleUploadAll = async () => {
    const eligible = fileQueue.filter(
      (item) => item.status === "Waiting" || item.status === "Failed"
    );
    if (eligible.length === 0) return;

    setIsUploadingAny(true);
    setGeneralMessage(null);
    let successCount = 0;
    let failCount = 0;

    for (const item of eligible) {
      const ok = await uploadSingleItem(item);
      if (ok) successCount++;
      else failCount++;
    }

    setIsUploadingAny(false);

    if (failCount === 0 && successCount > 0) {
      setGeneralMessage({
        type: "success",
        text:
          successCount === 1
            ? "Document uploaded successfully."
            : `All ${successCount} documents uploaded successfully.`,
      });
    } else if (failCount > 0) {
      setGeneralMessage({
        type: "error",
        text: `Upload failed for ${failCount} document(s). Please retry.`,
      });
    }
  };

  const handleDeleteDocument = async (docId: string, docName: string) => {
    if (
      !confirm(
        `Are you sure you want to remove "${docName}" from the project repository?`
      )
    )
      return;

    setDeletingId(docId);
    try {
      await deleteProjectDocument(projectId, docId);
      await loadDocuments();
      setGeneralMessage({
        type: "success",
        text: `Document "${docName}" deleted successfully.`,
      });
    } catch {
      setGeneralMessage({
        type: "error",
        text: "Failed to delete document. Please retry.",
      });
    } finally {
      setDeletingId(null);
    }
  };

  const waitingCount = fileQueue.filter(
    (i) => i.status === "Waiting" || i.status === "Failed"
  ).length;

  return (
    <div
      className="doc-modal-overlay"
      onClick={isUploadingAny ? undefined : onClose}
      role="presentation"
    >
      <div
        className="doc-modal-container"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="doc-modal-title"
      >
        <header className="doc-modal-header">
          <div>
            <div className="doc-modal-eyebrow">
              <span className="live-dot" /> PROJECT REPOSITORY • {projectId}
            </div>
            <h2 id="doc-modal-title">Upload Project Documents</h2>
            <p>{projectName}</p>
          </div>
          <button
            className="doc-modal-close"
            onClick={onClose}
            disabled={isUploadingAny}
            aria-label="Close document repository modal"
            title="Close modal"
          >
            <X size={18} />
          </button>
        </header>

        <div className="doc-modal-body">
          {generalMessage && (
            <div
              className={`doc-alert ${
                generalMessage.type === "success"
                  ? "doc-alert-success"
                  : "doc-alert-error"
              }`}
              role="alert"
            >
              {generalMessage.type === "success" ? (
                <CheckCircle2 size={16} />
              ) : (
                <AlertCircle size={16} />
              )}
              <span>{generalMessage.text}</span>
            </div>
          )}

          {fetchError && (
            <div className="doc-alert doc-alert-error" role="alert">
              <AlertCircle size={16} />
              <span>{fetchError}</span>
              <button
                type="button"
                className="doc-inline-retry"
                onClick={loadDocuments}
              >
                Retry
              </button>
            </div>
          )}

          <section className="doc-upload-section">
            <h3 className="doc-section-title">
              <UploadCloud size={16} /> Select &amp; Upload Files
            </h3>

            <div
              ref={dropzoneRef}
              className={`doc-dropzone${isDragging ? " dragging" : ""}`}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              tabIndex={0}
              role="button"
              aria-label="Drag and drop documents here or click to browse files"
            >
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileChange}
                accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.jpg,.jpeg,.png"
                multiple
                style={{ display: "none" }}
                aria-hidden="true"
              />
              <div className="doc-dropzone-prompt">
                <UploadCloud size={30} className="dropzone-icon" />
                <p>
                  <strong>Drag &amp; drop documents here</strong>
                </p>
                <p className="doc-subprompt">
                  or <span className="browse-link">browse files</span>
                </p>
                <div className="doc-format-badges">
                  {["PDF","DOC","DOCX","XLS","XLSX","CSV","JPG","JPEG","PNG"].map(
                    (fmt) => (
                      <span key={fmt}>{fmt}</span>
                    )
                  )}
                </div>
                <small className="doc-size-notice">
                  Maximum file size: <strong>{MAX_FILE_SIZE_MB} MB</strong> per
                  file
                </small>
              </div>
            </div>

            <div className="doc-form-row">
              <div className="doc-form-group">
                <label htmlFor="doc-category-select">
                  Default Classification
                </label>
                <select
                  id="doc-category-select"
                  value={globalCategory}
                  onChange={(e) =>
                    setGlobalCategory(e.target.value as DocumentCategory)
                  }
                >
                  {CATEGORIES.map((cat) => (
                    <option key={cat} value={cat}>
                      {cat}
                    </option>
                  ))}
                </select>
              </div>
              <div className="doc-form-group">
                <label htmlFor="doc-description-input">
                  Notes / Brief Description (Optional)
                </label>
                <input
                  id="doc-description-input"
                  type="text"
                  placeholder="e.g. Approved statutory environmental clearance by State Authority"
                  value={globalDescription}
                  onChange={(e) => setGlobalDescription(e.target.value)}
                />
              </div>
            </div>

            {fileQueue.length > 0 && (
              <div className="doc-queue-container">
                <div className="doc-queue-header">
                  <span>Selected Files Queue ({fileQueue.length})</span>
                  {waitingCount > 0 && (
                    <button
                      type="button"
                      className="doc-btn-primary"
                      onClick={handleUploadAll}
                      disabled={isUploadingAny}
                      aria-label={`Upload ${waitingCount} queued file${waitingCount > 1 ? "s" : ""}`}
                    >
                      {isUploadingAny ? (
                        <>
                          <Loader2 size={14} className="spinner" /> Uploading…
                        </>
                      ) : (
                        <>
                          <Upload size={14} /> Upload {waitingCount} File
                          {waitingCount > 1 ? "s" : ""}
                        </>
                      )}
                    </button>
                  )}
                </div>

                <div className="doc-queue-list">
                  {fileQueue.map((item) => (
                    <div
                      className={`doc-queue-item status-${item.status.toLowerCase()}`}
                      key={item.id}
                    >
                      <div className="doc-queue-main">
                        {getFileIcon(item.name)}
                        <div className="doc-queue-info">
                          <strong>{item.name}</strong>
                          <span className="doc-queue-meta">
                            {item.mimeType} &bull; {formatBytes(item.size)}
                          </span>
                          {item.errorMessage && (
                            <span className="doc-queue-error" role="alert">
                              {item.errorMessage}
                            </span>
                          )}
                        </div>
                      </div>

                      <div className="doc-queue-status">
                        <span
                          className={`doc-status-badge badge-${item.status.toLowerCase()}`}
                        >
                          {item.status === "Uploading" && (
                            <Loader2 size={11} className="spinner" />
                          )}
                          {item.status === "Uploaded" && (
                            <CheckCircle2 size={11} />
                          )}
                          {item.status === "Failed" && (
                            <AlertCircle size={11} />
                          )}
                          {item.status}
                        </span>

                        {item.status === "Failed" && (
                          <button
                            type="button"
                            className="doc-retry-btn"
                            onClick={() => uploadSingleItem(item)}
                            title="Retry upload"
                            aria-label={`Retry uploading ${item.name}`}
                          >
                            <RefreshCw size={12} /> Retry
                          </button>
                        )}

                        <button
                          type="button"
                          className="doc-remove-queue-btn"
                          onClick={() => removeQueueItem(item.id)}
                          disabled={item.status === "Uploading"}
                          title="Remove from queue"
                          aria-label={`Remove ${item.name} from queue`}
                        >
                          <X size={13} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="doc-list-section">
            <div className="doc-list-header">
              <h3 className="doc-section-title">
                <FolderOpen size={16} /> Cataloged Project Documents
              </h3>
              <span className="doc-count-badge">
                {documents.length}{" "}
                {documents.length === 1 ? "document" : "documents"} (
                {formatBytes(
                  documents.reduce((acc, d) => acc + d.file_size_bytes, 0)
                )}
                )
              </span>
            </div>

            {isLoading ? (
              <div
                className="doc-loading"
                aria-live="polite"
                aria-label="Loading documents"
              >
                <Loader2 size={22} className="spinner" />
                <span>Loading project documents…</span>
              </div>
            ) : documents.length === 0 ? (
              <div className="doc-empty-state">
                <FileText size={32} />
                <p>No project documents cataloged yet</p>
                <small>
                  Drag and drop DPRs, statutory clearances, or surveys above to
                  link official documentation with {projectId}.
                </small>
              </div>
            ) : (
              <div className="doc-table-wrap">
                <div className="doc-table">
                  <div className="doc-table-head">
                    <span>DOCUMENT NAME</span>
                    <span>CATEGORY</span>
                    <span>SIZE</span>
                    <span>UPLOADED</span>
                    <span>ACTIONS</span>
                  </div>
                  {documents.map((doc) => (
                    <div className="doc-table-row" key={doc.document_id}>
                      <div className="doc-cell-name">
                        {getFileIcon(doc.filename)}
                        <div>
                          <strong title={doc.filename}>{doc.filename}</strong>
                          {doc.description && (
                            <small title={doc.description}>
                              {doc.description}
                            </small>
                          )}
                        </div>
                      </div>

                      <div className="doc-cell-category">
                        <span className="doc-category-pill">
                          {doc.category ?? doc.mime_type}
                        </span>
                      </div>

                      <div className="doc-cell-size">
                        {formatBytes(doc.file_size_bytes)}
                      </div>

                      <div className="doc-cell-date">
                        <span>
                          {new Date(doc.uploaded_at).toLocaleDateString(
                            "en-IN",
                            {
                              day: "2-digit",
                              month: "short",
                              year: "numeric",
                            }
                          )}
                        </span>
                        <small>{doc.uploader}</small>
                      </div>

                      <div className="doc-cell-actions">
                        <a
                          href={getDocumentDownloadUrl(
                            projectId,
                            doc.document_id
                          )}
                          target="_blank"
                          rel="noreferrer"
                          className="doc-action-btn"
                          title="Download document"
                          aria-label={`Download ${doc.filename}`}
                        >
                          <Download size={14} />
                        </a>
                        <button
                          type="button"
                          className="doc-action-btn danger"
                          onClick={() =>
                            handleDeleteDocument(doc.document_id, doc.filename)
                          }
                          disabled={deletingId === doc.document_id}
                          title="Delete document"
                          aria-label={`Delete ${doc.filename}`}
                        >
                          {deletingId === doc.document_id ? (
                            <Loader2 size={14} className="spinner" />
                          ) : (
                            <Trash2 size={14} />
                          )}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
