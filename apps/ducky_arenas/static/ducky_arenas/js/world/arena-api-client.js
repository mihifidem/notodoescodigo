export class ArenaApiClient {
    constructor(root, csrfToken) {
        this.root = root;
        this.csrfToken = csrfToken;
    }

    async post(url, payload, keepalive = false) {
        const response = await fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            keepalive,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': this.readCookie('csrftoken') || this.csrfToken,
            },
            body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || `Error HTTP ${response.status}`);
        }
        return data;
    }

    async saveState(state, keepalive = false) {
        return this.post(this.root.dataset.stateUrl, state, keepalive);
    }

    async transitionMap(url, state) {
        const result = await this.post(url, state);
        if (
            typeof result.redirectUrl !== 'string'
            || !result.redirectUrl.startsWith('/')
            || result.redirectUrl.startsWith('//')
        ) {
            throw new Error('Django devolvió una ruta de mapa no válida.');
        }
        window.location.assign(result.redirectUrl);
    }

    async acknowledgeEvolution(url, eraCode) {
        return this.post(url, { era_code: eraCode });
    }

    async launchPortal(portal, state) {
        const result = await this.post(portal.launch_url, state);
        if (
            typeof result.redirectUrl !== 'string'
            || !result.redirectUrl.startsWith('/')
            || result.redirectUrl.startsWith('//')
        ) {
            throw new Error('Django devolvió una ruta de destino no válida.');
        }
        window.location.assign(result.redirectUrl);
    }

    readCookie(name) {
        const entry = document.cookie
            .split('; ')
            .find((part) => part.startsWith(`${name}=`));
        return entry
            ? decodeURIComponent(entry.slice(name.length + 1))
            : '';
    }
}
