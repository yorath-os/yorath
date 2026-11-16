import { Component, type ReactNode } from 'react'
import { StateView } from './ui/StateView'

// One screen crashing must not take down the shell: the menu stays, so the user can go elsewhere.
export default class ScreenBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="a3-desk__pad">
        <StateView
          kind="error"
          surface="dark"
          title="This screen failed to open"
          description="Something went wrong in the interface. Open another section or reload the page."
        />
      </div>
    )
  }
}
