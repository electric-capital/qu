/**
 * Authentication utilities
 */

/**
 * Check if user has a valid session by calling the /app/api/me endpoint.
 * Returns user info if authenticated, null otherwise.
 */
export async function checkSession(): Promise<{
  email: string;
  name: string;
  google_services_connected: boolean;
  has_any_service_connected: boolean;
  is_admin: boolean;
  is_impersonating: boolean;
  impersonator_email: string | null;
  impersonator_name: string | null;
  default_model: string | null;
  // Settings > Appearance colour scheme: "light" | "dark" | null (= auto).
  theme?: string | null;
  // Settings > Appearance colour theme id ("prototype" | "electric-blue" | "alloy" | "recall");
  // null = default (prototype).
  color_theme?: string | null;
  enabled_features?: string[];
} | null> {
  try {
    const response = await fetch('/app/api/me', {
      credentials: 'include',
    });
    if (response.ok) {
      return await response.json();
    }
    return null;
  } catch {
    return null;
  }
}
