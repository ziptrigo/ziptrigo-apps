/**
 * JWT Authentication utility for QR Code App
 *
 * Manages JWT tokens in localStorage and provides helper functions
 * for authenticated API requests.
 */

const Auth = (function () {
    'use strict';

    const ACCESS_TOKEN_KEY = 'qr_access_token';
    const REFRESH_TOKEN_KEY = 'qr_refresh_token';

    /**
     * Store JWT tokens in localStorage
     */
    function setTokens(accessToken, refreshToken) {
        if (accessToken) {
            localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
        }
        if (refreshToken) {
            localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
        }
    }

    /**
     * Get access token from localStorage
     */
    function getAccessToken() {
        return localStorage.getItem(ACCESS_TOKEN_KEY);
    }

    /**
     * Get refresh token from localStorage
     */
    function getRefreshToken() {
        return localStorage.getItem(REFRESH_TOKEN_KEY);
    }

    /**
     * Clear all tokens from localStorage
     */
    function clearTokens() {
        localStorage.removeItem(ACCESS_TOKEN_KEY);
        localStorage.removeItem(REFRESH_TOKEN_KEY);
    }

    /**
     * Check if user is authenticated
     */
    function isAuthenticated() {
        return !!getAccessToken();
    }

    /**
     * Refresh access token using refresh token
     */
    async function refreshAccessToken() {
        const refreshToken = getRefreshToken();
        if (!refreshToken) {
            return false;
        }

        try {
            const response = await fetch('/api/token/refresh', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    refresh: refreshToken,
                }),
            });

            if (response.ok) {
                const data = await response.json();
                setTokens(data.access, null);
                return true;
            } else {
                clearTokens();
                return false;
            }
        } catch (error) {
            console.error('Token refresh error:', error);
            clearTokens();
            return false;
        }
    }

    /**
     * Make an authenticated API request
     * Automatically refreshes token if needed
     */
    async function authenticatedFetch(url, options = {}) {
        let accessToken = getAccessToken();

        if (!accessToken) {
            throw new Error('Not authenticated');
        }

        // Add Authorization header
        const headers = {
            ...options.headers,
            Authorization: `Bearer ${accessToken}`,
        };

        // First attempt
        let response = await fetch(url, {
            ...options,
            headers,
        });

        // If 401, try refreshing token
        if (response.status === 401) {
            const refreshed = await refreshAccessToken();
            if (refreshed) {
                // Retry with new token
                accessToken = getAccessToken();
                headers.Authorization = `Bearer ${accessToken}`;
                response = await fetch(url, {
                    ...options,
                    headers,
                });
            } else {
                // Refresh failed, redirect to login
                window.location.href = '/login/';
                throw new Error('Authentication failed');
            }
        }

        return response;
    }

    /**
     * Login with email and password
     */
    async function login(email, password) {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                email,
                password,
            }),
        });

        if (response.ok) {
            const data = await response.json();
            setTokens(data.access, data.refresh);
            return { success: true, data };
        } else {
            const error = await response.json();
            return { success: false, error };
        }
    }

    /**
     * Signup with name, email and password
     */
    async function signup(name, email, password) {
        const response = await fetch('/api/auth/signup', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                name,
                email,
                password,
            }),
        });

        if (response.ok) {
            const data = await response.json();
            return { success: true, data };
        } else {
            const error = await response.json();
            return { success: false, error };
        }
    }

    /**
     * Logout - clear tokens and redirect to login
     */
    function logout() {
        clearTokens();
        window.location.href = '/login/';
    }

    /**
     * Get current authenticated user info
     */
    async function getCurrentUser() {
        try {
            const response = await authenticatedFetch('/api/auth/me');
            if (response.ok) {
                return await response.json();
            }
            return null;
        } catch (error) {
            console.error('Get current user error:', error);
            return null;
        }
    }

    // Configure htmx to add Authorization header to all requests
    if (typeof htmx !== 'undefined') {
        document.addEventListener('htmx:configRequest', (event) => {
            const accessToken = getAccessToken();
            if (accessToken) {
                event.detail.headers['Authorization'] = `Bearer ${accessToken}`;
            }
        });

        // Handle 401 responses from htmx requests
        document.addEventListener('htmx:responseError', async (event) => {
            if (event.detail.xhr.status === 401 && isAuthenticated()) {
                // Try to refresh token
                const refreshed = await refreshAccessToken();
                if (refreshed) {
                    // Retry the request
                    htmx.trigger(event.detail.elt, event.detail.requestConfig.verb, {
                        target: event.detail.target,
                    });
                } else {
                    // Redirect to login
                    logout();
                }
            }
        });
    }

    // Public API
    return {
        setTokens,
        getAccessToken,
        getRefreshToken,
        clearTokens,
        isAuthenticated,
        refreshAccessToken,
        authenticatedFetch,
        login,
        signup,
        logout,
        getCurrentUser,
    };
})();

// Make it globally available
window.Auth = Auth;
