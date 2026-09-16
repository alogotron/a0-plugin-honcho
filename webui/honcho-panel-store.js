import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import { store as chatsStore } from "/components/sidebar/chats/chats-store.js";
import {
  toastFrontendError,
  toastFrontendSuccess,
  toastFrontendWarning,
} from "/components/notifications/notification-store.js";

const API_PATH = "/plugins/honcho/honcho_status";

const model = {
  data: null,
  loading: false,
  busy: false,
  collapsed: localStorage.getItem("honcho-panel-collapsed") !== "0",
  width: Number(localStorage.getItem("honcho-panel-width") || 300),
  loadSeq: 0,

  get contextId() {
    return chatsStore?.getSelectedChatId?.() || globalThis.getContext?.() || "";
  },

  get counts() {
    return this.data?.outbox?.counts || {};
  },

  get worker() {
    return this.data?.outbox?.worker || {};
  },

  get scope() {
    return this.data?.scope || { view: "global", strategy: "off", resolved: null, sdk_support: false };
  },

  get scopeLabel() {
    const scope = this.scope;
    if (!scope.resolved) return "global";
    return scope.sdk_support ? scope.resolved : `${scope.resolved} (unsupported SDK)`;
  },

  get statusClass() {
    if (!this.data) return "warn";
    if (!this.data.configured) return "down";
    if ((this.counts.dead || 0) > 0 || !this.worker.running) return "warn";
    return "live";
  },

  get statusLabel() {
    if (!this.data) return "Checking…";
    if (!this.data.configured) {
      if (!this.data.sdk_available) return "SDK missing";
      return this.data.mode === "hosted" ? "API key not set" : "Configuration incomplete";
    }
    if ((this.counts.dead || 0) > 0) return `Needs attention · ${this.counts.dead} dead`;
    if (!this.worker.running && this.data.capture_enabled) return "Memory ready · worker stopped";
    return `Memory ready · ${this.data.mode}`;
  },

  async onMount(watch) {
    watch("$store.chats.selected", () => this.refresh());
    watch("$store.honcho.collapsed", value => {
      localStorage.setItem("honcho-panel-collapsed", value ? "1" : "0");
    });
    await this.refresh();
  },

  cleanup() {},

  async call(action, extra = {}) {
    return callJsonApi(API_PATH, { action, context_id: this.contextId, ...extra });
  },

  async refresh() {
    const seq = ++this.loadSeq;
    this.loading = true;
    try {
      const data = await this.call("status");
      if (seq === this.loadSeq) this.data = data;
    } catch (error) {
      if (seq === this.loadSeq) toastFrontendError(String(error), "Honcho status failed");
    } finally {
      if (seq === this.loadSeq) this.loading = false;
    }
  },

  async operate(action, successMessage, extra = {}) {
    if (this.busy) return;
    this.busy = true;
    try {
      const result = await this.call(action, extra);
      if (!result?.ok) {
        toastFrontendWarning(result?.detail || `${action} did not complete.`, "Honcho Memory Hub");
      } else if (successMessage) {
        toastFrontendSuccess(successMessage, "Honcho Memory Hub");
      }
      await this.refresh();
      return result;
    } catch (error) {
      toastFrontendError(String(error), `Honcho ${action} failed`);
      return null;
    } finally {
      this.busy = false;
    }
  },

  async ping() {
    const result = await this.operate("ping", "");
    if (!result) return;
    if (result.checks?.api?.ok && result.checks?.mcp?.ok) {
      toastFrontendSuccess("API and MCP checks passed.", "Honcho connection check");
    } else {
      toastFrontendWarning("One or more checks failed.", "Honcho connection check");
    }
  },

  clearCache() {
    return this.operate("clear_cache", "Context cache cleared.");
  },

  wake() {
    return this.operate("wake", "Delivery worker was awakened.");
  },

  drain() {
    return this.operate("drain", "Outbox drained.", { timeout: 5 });
  },

  retryDeadLetters() {
    return this.operate("retry_dead_letters", "Dead letters were re-queued.", { limit: 100 });
  },

  startResize(event) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = this.width;
    const move = next => {
      this.width = Math.min(600, Math.max(240, startWidth + next.clientX - startX));
      localStorage.setItem("honcho-panel-width", String(this.width));
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  },
};

export const store = createStore("honcho", model);
