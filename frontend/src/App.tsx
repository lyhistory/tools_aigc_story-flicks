import { Tabs } from 'antd';
import StoryForm from './components/StoryFrom';
import SocialPublisher from './components/SocialPublisher';
import './App.css';
import './locales/index';

function App() {
  return (
    <div className="app" style={{ padding: 24, margin: '0 auto' }}>
      <Tabs
        defaultActiveKey="1"
        items={[
          {
            key: '1',
            label: 'Story Flicks Studio',
            children: <div className="appMainArea"><StoryForm /></div>,
          },
          {
            key: '2',
            label: 'Social Auto Publisher',
            children: <div className="appMainArea"><SocialPublisher /></div>,
          },
        ]}
      />
    </div>
  )
}

export default App;
