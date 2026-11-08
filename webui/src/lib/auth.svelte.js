// The signed-in user. `ready` turns true once the first /api/auth/me answers.

import { get, post } from "./api.js";

class Auth {
  user = $state(null); // {id, email, role}
  ready = $state(false);

  get isAdmin() {
    return this.user?.role === "admin";
  }

  async load() {
    try {
      this.user = await get("/api/auth/me", { quiet401: true });
    } catch {
      this.user = null;
    } finally {
      this.ready = true;
    }
  }

  async login(email, password) {
    this.user = await post("/api/auth/login", { email, password }, { quiet401: true });
  }

  /** Sign in with a set-password link. */
  async redeem(secret, password) {
    this.user = await post(`/api/auth/links/${encodeURIComponent(secret)}`, { password }, { quiet401: true });
  }

  async logout() {
    try {
      await post("/api/auth/logout");
    } finally {
      this.user = null;
    }
  }

  signedOut() {
    this.user = null;
  }
}

export const auth = new Auth();
