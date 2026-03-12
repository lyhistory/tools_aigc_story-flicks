import axios from 'axios';

interface RequestConfig {
    url: string;
    method: string;
    data?: object;
    headers?: object;
    params?: object;
}

let baseUrl = 'http://127.0.0.1:8888';

export function request<T>(config: RequestConfig): Promise<T> {
    return new Promise((resolve, reject) => {
        axios.request({
            ...config,
            baseURL: baseUrl
        }).then((response) => {
            resolve(response.data);
        }).catch((error) => {
            reject(error);
        });
    });

}