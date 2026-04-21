# Auto-Publish Video Integration & Translation Improvements

This implementation plan details the updated approach to address the subtitle translation workflow and social media publishing, based on your feedback.

---

## 1. Subtitle Translation UI & LLM Retranslation Workflow

Since Chinese translations currently happen purely on-the-fly during the final video rendering (Step 2), we will shift the translation discovery to Step 1 so that you can view and edit them in the Storyboard Editor.

### Workflow Changes
1. **Pre-Translate during Step 1**: When generating the storyboard, if the user ticks "Chinese Subtitle Translation", the backend will use Google Translate to pre-translate the scene scripts. These translations will be returned to the frontend as a new field: `scene.chinese_translation`.
2. **Storyboard Editor UI**: 
   - A new visual section will be added beneath the English script in each scene showing the Chinese translation.
   - You will be able to manually tweak these translations.
3. **"Re-translate using LLM" Button**:
   - A button will be placed next to the translations. Clicking it will trigger a new backend endpoint (e.g., `/api/translate_scene`).
   - The backend will send the English script of that specific scene to the selected Text LLM, asking for a child-friendly translation, which is then updated on the UI.
4. **Video Assembly (Step 2)**: 
   - When generating the final video, the backend will prioritize mapping the injected `chinese_translation` from the UI instead of reaching out to Google Translate.

---

## 2. Dedicated Auto-Publishing Application

To give you maximum flexibility and allow you to upload previously generated (or entirely external) videos, we will create a dedicated **"Publishing Hub"** rather than burying it inside the video generation process.

### Architecture

#### Frontend: New "Social Hub" Page
- A new top-level Navigation tab (e.g., "Social Publishing").
- The page will contain:
  1. **Upload / Select Video**: A file picker to upload existing videos or pick from tasks.
  2. **Metadata Form**: Fields to type in the Title, Description, and Tags.
  3. **Platform Selectors**: Toggle switches for Douyin, Xiaohongshu, and WeChat Channels.
  4. **Status Dashboard**: See which accounts are currently logged in and view the upload status logs.

#### Backend: Headless Browser Automation Module
Because these platforms block open APIs, we will build a small internal server using **Playwright**:
1. **Authentication Management**: Expose endpoints to generate standard QR codes. You scan the QR code using your Douyin/WeChat/XHS mobile apps, and the backend securely saves your session tokens.
2. **Automation Scripts**: 
   - `publish_douyin.py` -> Controls headless browser navigating to `creator.douyin.com`.
   - `publish_xhs.py` -> Navigates to `creator.xiaohongshu.com`.
   - `publish_wechat.py` -> Navigates to `channels.weixin.qq.com`.
   *These scripts will programmatically manipulate the browser GUI: opening the upload modal, attaching the video byte buffer, filling text inputs, and clicking publish.*

### User Review Required
> [!IMPORTANT]  
> 1. Because the Social Media publishing requires new python packages like Playwright and setting up a separate page, we should break this into two phases. Do you approve doing Phase 1 (Translation UI & LLM Retranslation) first, and then executing Phase 2 (Social Media Automation Page)?
