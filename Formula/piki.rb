class Piki < Formula
  include Language::Python::Virtualenv

  desc "Team wiki context hub for coding agents"
  homepage "https://github.com/cmux-aim-netlog/piki"
  url "https://files.pythonhosted.org/packages/source/p/piki/piki-0.1.0.tar.gz"
  sha256 "PLACEHOLDER"
  license "MIT"

  depends_on "python@3.12"

  resource "typer" do
    url "https://files.pythonhosted.org/packages/source/t/typer/typer-0.12.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.0.0.tar.gz"
    sha256 "PLACEHOLDER"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "piki", shell_output("#{bin}/piki --help")
  end
end
