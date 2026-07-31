import axios from 'axios';

interface RequestConfig {
    url: string;
    method: string;
    data?: object;
    headers?: object;
    params?: object;
}

export const API_BASE_URL = 'http://127.0.0.1:8889';

export function request<T>(config: RequestConfig): Promise<T> {
    return new Promise((resolve, reject) => {
        axios.request({
            ...config,
            baseURL: API_BASE_URL
        }).then((response) => {
            resolve(response.data);
        }).catch((error) => {
            reject(error);
        });
    });

}
