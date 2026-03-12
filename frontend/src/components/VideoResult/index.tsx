import { useRef } from 'react';
import { Spin } from 'antd';
import { useVideoStore } from "../../stores/index";
import styles from './index.module.css'
export default function VideoResult() {

    const { videoUrl, assembling } = useVideoStore();
    const videoRef = useRef<HTMLVideoElement>(null);
    if (!videoUrl && !assembling) {
        return null;
    }
    return (
        <div className={styles.videoContainer} key={videoUrl || 'assembling'}>
            <Spin spinning={assembling} tip="Assembling final video..." size="large">
                {videoUrl ? (
                    <video ref={videoRef} controls className={styles.videoEl}>
                        <source src={videoUrl} type="video/mp4" />
                        Your browser does not support the video tag.
                    </video>
                ) : (
                    <div className={styles.videoEl} style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', background: '#000' }}>
                        <span style={{ color: '#fff' }}>Generating Video...</span>
                    </div>
                )}
            </Spin>
        </div>
    )
}