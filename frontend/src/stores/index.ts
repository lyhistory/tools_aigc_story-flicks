import { create } from 'zustand'

interface VideoStore {
    videoUrl: string;
    setVideoUrl: (url: string) => void;
    assembling: boolean;
    setAssembling: (val: boolean) => void;
}

export const useVideoStore = create<VideoStore>((set) => ({
  videoUrl: "",
  setVideoUrl: (url: string) => set({ videoUrl: url }),
  assembling: false,
  setAssembling: (val: boolean) => set({ assembling: val }),
}))